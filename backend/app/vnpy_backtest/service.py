"""Dispatch registered single-symbol and portfolio vn.py minute backtests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from collections import defaultdict
from collections.abc import Callable
from statistics import stdev
from typing import Any
from uuid import uuid4

import polars as pl
from vnpy.trader.constant import Direction

from app.vnpy_backtest.local_data import bars_from_minute_frame
from app.vnpy_backtest.portfolio import DailyContextBuilder, MultiSymbolNextBarOpenEngine
from app.vnpy_backtest.strategies.base import StrategySpec
from app.vnpy_backtest.strategies.registry import get_strategy


@dataclass(frozen=True)
class VnpyMinuteBacktestConfig:
    start: date
    end: date
    symbols: tuple[str, ...] = ()
    strategy_id: str = "opening_volume_portfolio"
    initial_capital: float = 100_000.0
    commission_pct: float = 0.0002
    stamp_tax_pct: float = 0.001
    slippage_bps: float = 5.0
    lot_size: int = 100
    max_volume_ratio: float | None = None
    max_buy_volume_ratio: float | None = 1.0
    max_sell_volume_ratio: float | None = 1.0
    max_positions: int = 10
    position_sizing: str = "equal"
    candidate_sort: str = "volume_ratio"
    entry_fill: str = "next_minute_open"
    exit_fill: str = "next_minute_open"
    force_close_at_end: bool = True
    params: dict[str, Any] = field(default_factory=dict)
    is_cancelled: Callable[[], bool] | None = None
    on_progress: Callable[[int, int, date, float], None] | None = None

    @property
    def normalized_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.strip().upper() for item in self.symbols if item and item.strip()))


class VnpyMinuteBacktestService:
    """Strategy registry dispatcher over TickFlow's stored local minute data."""

    def __init__(self, repo) -> None:
        self.repo = repo

    def run(self, config: VnpyMinuteBacktestConfig) -> dict:
        if config.end < config.start:
            raise ValueError("end date must not precede start date")
        spec = get_strategy(config.strategy_id)
        if spec is None:
            raise ValueError(f"不支持的 vn.py 策略: {config.strategy_id}")
        symbols = config.normalized_symbols
        if not spec.min_symbols <= len(symbols) <= spec.max_symbols:
            raise ValueError(f"{spec.name} 支持 {spec.min_symbols}–{spec.max_symbols} 只股票")
        return self._run_portfolio(config, spec, symbols)

    def _settings(self, config: VnpyMinuteBacktestConfig) -> dict[str, Any]:
        return {
            **config.params,
            "initial_cash": config.initial_capital,
            "default_lot_size": config.lot_size,
            "max_buy_volume_ratio": config.max_buy_volume_ratio,
            "max_sell_volume_ratio": config.max_sell_volume_ratio,
            "max_positions": config.max_positions,
            "position_sizing": config.position_sizing,
            "candidate_sort": config.candidate_sort,
            "entry_fill": config.entry_fill,
            "exit_fill": config.exit_fill,
            "force_close_at_end": config.force_close_at_end,
            "commission_rate": config.commission_pct,
            "stamp_tax_rate": config.stamp_tax_pct,
            "slippage_rate": config.slippage_bps / 10_000,
        }

    def _run_portfolio(self, config: VnpyMinuteBacktestConfig, spec: StrategySpec, symbols: tuple[str, ...]) -> dict:
        instrument_names, instrument_limit_pcts = self._instrument_metadata(symbols)
        engine = MultiSymbolNextBarOpenEngine(
            initial_cash=config.initial_capital,
            commission_rate=config.commission_pct,
            stamp_tax_rate=config.stamp_tax_pct,
            slippage_rate=config.slippage_bps / 10_000,
            min_commission=float(config.params.get("min_commission", 5.0)),
            max_volume_ratio=config.max_volume_ratio,
            max_buy_volume_ratio=config.max_buy_volume_ratio,
            max_sell_volume_ratio=config.max_sell_volume_ratio,
            max_positions=config.max_positions,
            position_sizing=config.position_sizing,
            reserve_ratio=float(config.params.get("cash_reserve_ratio", 0.03)),
            candidate_sort=config.candidate_sort,
            score_weights=config.params.get("scoring", {}),
            score_min=config.params.get("score_min"),
            score_max=config.params.get("score_max"),
            watchlist_order=list(symbols),
            instrument_names=instrument_names,
            instrument_limit_pcts=instrument_limit_pcts,
        )
        strategy = spec.strategy_class(dict(config.params))
        daily_context = DailyContextBuilder()
        days_seen = 0
        # Build completed-day context before the requested window as well.  The
        # opening-breakout strategy needs yesterday's same-minute cumulative
        # volume and four prior daily closes on the first requested trade day.
        warmup_start = config.start - timedelta(days=20)
        try:
            trading_days = self.repo.minute_trading_days(config.start, config.end)
            total_days = len(trading_days)
        except AttributeError:
            trading_days = []
            total_days = max((config.end - config.start).days + 1, 1)
        final_trading_day = trading_days[-1] if trading_days else config.end
        final_bars_by_symbol = None
        final_references = None
        for trading_day, frame in self.repo.iter_minute_days(list(symbols), warmup_start, config.end):
            if config.is_cancelled and config.is_cancelled():
                raise RuntimeError("回测已取消")
            bars_by_symbol = self._bars_by_symbol(frame, symbols)
            if not bars_by_symbol:
                continue
            if trading_day < config.start:
                daily_context.add_day(bars_by_symbol)
                continue
            references = daily_context.references()
            engine.run_day(
                bars_by_symbol,
                strategy,
                references,
                allow_entries=not (config.force_close_at_end and trading_day == final_trading_day),
            )
            if trading_day == final_trading_day:
                final_bars_by_symbol = bars_by_symbol
                final_references = references
            daily_context.add_day(bars_by_symbol)
            days_seen += 1
            if config.on_progress:
                current_equity = engine.equity_curve[-1]["value"] if engine.equity_curve else config.initial_capital
                config.on_progress(days_seen, max(total_days, 1), trading_day, current_equity)
        if days_seen == 0:
            raise ValueError("所选日期范围内没有本地分钟 K 数据")
        if config.force_close_at_end and final_bars_by_symbol is not None:
            engine.force_close_at_end(final_bars_by_symbol, final_references or {})
        result = engine.result()
        end_balance = result.equity_curve[-1]["value"] if result.equity_curve else config.initial_capital
        timeline = self._timeline_indexes(result.equity_curve)
        completed_trades = self._portfolio_completed_trades(result.fills, instrument_names, timeline)
        metrics, drawdown_curve = self._portfolio_metrics(
            initial_capital=config.initial_capital,
            equity_curve=result.equity_curve,
            completed_trades=completed_trades,
            fills=result.fills,
            trading_days=days_seen,
        )
        positions = self._portfolio_positions(
            result.positions, result.last_prices, instrument_names, end_balance, timeline,
        )
        daily_ledger = self._daily_ledger(
            result.fills, completed_trades, result.equity_curve, instrument_names, config.initial_capital,
        )
        return {
            "run_id": uuid4().hex,
            "config": self._result_config(config, spec, list(symbols), self._settings(config)),
            "stats": {
                "mode": "vnpy_portfolio",
                "benchmark_status": "local_unavailable",
                "symbols_requested": len(symbols),
                "trading_days": days_seen,
                "rejection_count": len(result.rejections),
                "open_position_count": len(positions),
                **metrics,
            },
            "equity_curve": result.equity_curve,
            "drawdown_curve": drawdown_curve, "benchmark_curve": [],
            "trades": completed_trades,
            "fills": [self._portfolio_fill_to_trade(fill, instrument_names) for fill in result.fills],
            "daily_ledger": daily_ledger,
            "per_symbol_stats": self._portfolio_symbol_stats(completed_trades, result.positions, instrument_names, result.last_prices),
            "positions": positions,
            "signal_diagnostics": [self._portfolio_signal_to_dict(signal, instrument_names) for signal in result.signals],
            "rejections": [item.__dict__ for item in result.rejections],
            "strategy_info": {"id": spec.id, "name": spec.name, "source": "vnpy"},
        }

    def _bars_by_symbol(self, frame: pl.DataFrame, symbols: tuple[str, ...]) -> dict[str, list]:
        wanted = set(symbols)
        result: dict[str, list] = {}
        for sub in frame.partition_by("symbol", maintain_order=False):
            if sub.is_empty():
                continue
            symbol = sub["symbol"][0]
            if symbol in wanted:
                result[symbol] = bars_from_minute_frame(symbol, sub)
        return result

    def _instrument_metadata(self, symbols: tuple[str, ...]) -> tuple[dict[str, str], dict[str, float]]:
        """Read optional security metadata without ever falling back to an online source."""
        try:
            df = self.repo.get_instruments().filter(pl.col("symbol").is_in(symbols))
            names: dict[str, str] = {}
            limit_pcts: dict[str, float] = {}
            for row in df.to_dicts():
                symbol = row["symbol"]
                names[symbol] = row.get("name") or ""
                raw_limit = row.get("limit_up")
                if raw_limit is None:
                    continue
                try:
                    limit_pct = float(raw_limit)
                except (TypeError, ValueError):
                    continue
                # TickFlow's security table documents this as a percentage.  Accept
                # both 10 and 0.10 so older local tables work too.
                if limit_pct > 1:
                    limit_pct /= 100
                if 0 < limit_pct <= 1:
                    limit_pcts[symbol] = limit_pct
            return names, limit_pcts
        except Exception:  # noqa: BLE001
            return {}, {}

    @staticmethod
    def _portfolio_fill_to_trade(fill, instrument_names: dict[str, str] | None = None) -> dict:
        return {"symbol": fill.symbol, "name": (instrument_names or {}).get(fill.symbol) or None, "entry_date": fill.datetime.isoformat(sep=" "),
                "exit_date": None, "entry_datetime": fill.datetime.isoformat(sep=" "), "exit_datetime": None,
                "entry_price": fill.price, "exit_price": None, "pnl_pct": None, "pnl_amount": None,
                "duration": 0, "duration_minutes": 0, "exit_reason": fill.reason, "shares": fill.volume,
                "lots": None, "position_pct": fill.entry_position_pct, "entry_value": fill.price * fill.volume + fill.commission, "exit_value": None,
                "direction": VnpyMinuteBacktestService._direction_code(fill.direction), "commission": fill.commission, "stamp_tax": fill.stamp_tax,
                "slippage": fill.slippage, "signal_id": fill.signal_id,
                "portfolio_equity_before": fill.portfolio_equity_before}

    @staticmethod
    def _portfolio_completed_trades(fills, instrument_names: dict[str, str] | None = None, timeline: dict | None = None) -> list[dict]:
        """Pair portfolio fills FIFO so the UI receives real closed trades, not raw orders."""
        open_lots: dict[str, list[dict]] = {}
        result: list[dict] = []
        for fill in fills:
            lots = open_lots.setdefault(fill.symbol, [])
            if fill.direction == Direction.LONG:
                cost_per_share = fill.price + fill.commission / fill.volume
                lots.append({"datetime": fill.datetime, "price": cost_per_share, "volume": fill.volume,
                             "position_pct": fill.entry_position_pct})
                continue
            net_per_share = fill.price - (fill.commission + fill.stamp_tax) / fill.volume
            remaining = fill.volume
            while remaining > 0 and lots:
                lot = lots[0]
                volume = min(remaining, lot["volume"])
                pnl_amount = (net_per_share - lot["price"]) * volume
                result.append({
                    "symbol": fill.symbol, "name": (instrument_names or {}).get(fill.symbol) or None,
                    "entry_date": lot["datetime"].isoformat(sep=" "), "exit_date": fill.datetime.isoformat(sep=" "),
                    "entry_datetime": lot["datetime"].isoformat(sep=" "), "exit_datetime": fill.datetime.isoformat(sep=" "),
                    "entry_price": round(lot["price"], 6), "exit_price": round(net_per_share, 6),
                    "pnl_pct": round(net_per_share / lot["price"] - 1, 8) if lot["price"] else None,
                    "pnl_amount": round(pnl_amount, 2),
                    "duration": VnpyMinuteBacktestService._trading_day_duration(lot["datetime"], fill.datetime, timeline),
                    "duration_minutes": VnpyMinuteBacktestService._trading_minute_duration(lot["datetime"], fill.datetime, timeline),
                    "exit_reason": fill.reason, "shares": volume, "lots": None, "position_pct": lot["position_pct"],
                    "entry_value": round(lot["price"] * volume, 2), "exit_value": round(net_per_share * volume, 2),
                    "entry_signal_id": None, "exit_signal_id": fill.signal_id,
                })
                lot["volume"] -= volume
                remaining -= volume
                if lot["volume"] == 0:
                    lots.pop(0)
        return result

    @staticmethod
    def _portfolio_symbol_stats(trades: list[dict], positions, instrument_names: dict[str, str] | None = None, last_prices: dict[str, float] | None = None) -> list[dict]:
        by_symbol: dict[str, list[dict]] = {}
        for trade in trades:
            by_symbol.setdefault(trade["symbol"], []).append(trade)
        for symbol, position in positions.items():
            by_symbol.setdefault(symbol, [])
        return [
            {
                "symbol": symbol,
                "name": (instrument_names or {}).get(symbol) or None,
                "n_trades": len(rows),
                "closed_trade_count": len(rows),
                "realized_pnl": round(sum(float(row["pnl_amount"] or 0) for row in rows), 2),
                "total_return": round(
                    sum(float(row["pnl_amount"] or 0) for row in rows)
                    / sum(float(row["entry_value"] or 0) for row in rows),
                    8,
                ) if any(float(row["entry_value"] or 0) > 0 for row in rows) else 0.0,
                "win_rate": round(
                    sum(1 for row in rows if float(row["pnl_amount"] or 0) > 0) / len(rows), 6,
                ) if rows else None,
                "best": max((row["pnl_pct"] for row in rows if row["pnl_pct"] is not None), default=None),
                "worst": min((row["pnl_pct"] for row in rows if row["pnl_pct"] is not None), default=None),
                "open_volume": positions.get(symbol).volume if symbol in positions else 0,
                "open_market_value": round((positions[symbol].volume * (last_prices or {}).get(symbol, positions[symbol].average_cost)), 2) if symbol in positions else 0.0,
                "avg_holding_days": round(sum(float(row.get("duration") or 0) for row in rows) / len(rows), 2) if rows else None,
                "avg_holding_minutes": round(sum(float(row.get("duration_minutes") or 0) for row in rows) / len(rows), 1) if rows else None,
            }
            for symbol, rows in sorted(by_symbol.items())
        ]

    @staticmethod
    def _timeline_indexes(equity_curve: list[dict]) -> dict:
        timestamps = [datetime.fromisoformat(str(point["date"])) for point in equity_curve]
        minute_index = {item: index for index, item in enumerate(timestamps)}
        trading_days = {item: index for index, item in enumerate(dict.fromkeys(item.date() for item in timestamps))}
        return {"minute_index": minute_index, "trading_days": trading_days, "last_timestamp": timestamps[-1] if timestamps else None}

    @staticmethod
    def _trading_day_duration(entry: datetime, exit: datetime, timeline: dict | None) -> int:
        days = (timeline or {}).get("trading_days", {})
        return max(int(days.get(exit.date(), 0)) - int(days.get(entry.date(), 0)), 0)

    @staticmethod
    def _trading_minute_duration(entry: datetime, exit: datetime, timeline: dict | None) -> int:
        minutes = (timeline or {}).get("minute_index", {})
        if entry in minutes and exit in minutes:
            return max(int(minutes[exit]) - int(minutes[entry]), 0)
        return 0

    @staticmethod
    def _portfolio_positions(positions, last_prices: dict[str, float], instrument_names: dict[str, str], end_balance: float, timeline: dict) -> list[dict]:
        last_timestamp = timeline.get("last_timestamp")
        result = []
        for symbol, position in positions.items():
            if position.volume <= 0:
                continue
            mark_price = last_prices.get(symbol, position.average_cost)
            market_value = position.volume * mark_price
            entry_at = position.entry_datetime
            result.append({
                "symbol": symbol, "name": instrument_names.get(symbol) or None, "volume": position.volume,
                "average_cost": round(position.average_cost, 6), "mark_price": round(mark_price, 6),
                "market_value": round(market_value, 2), "unrealized_pnl": round((mark_price - position.average_cost) * position.volume, 2),
                "unrealized_pnl_pct": round(mark_price / position.average_cost - 1, 8) if position.average_cost else None,
                "position_pct": round(market_value / end_balance, 8) if end_balance else None,
                "entry_date": entry_at.isoformat(sep=" ") if entry_at else None,
                "holding_days": VnpyMinuteBacktestService._trading_day_duration(entry_at, last_timestamp, timeline) if entry_at and last_timestamp else 0,
                "holding_minutes": VnpyMinuteBacktestService._trading_minute_duration(entry_at, last_timestamp, timeline) if entry_at and last_timestamp else 0,
            })
        return sorted(result, key=lambda item: item["market_value"], reverse=True)

    @staticmethod
    def _daily_ledger(fills, completed_trades: list[dict], equity_curve: list[dict], instrument_names: dict[str, str], initial_capital: float) -> list[dict]:
        rows: dict[str, dict] = {}
        for point in equity_curve:
            day = str(point["date"])[:10]
            rows.setdefault(day, {"date": day, "buy_count": 0, "sell_count": 0, "buy_amount": 0.0, "sell_amount": 0.0,
                                  "commission": 0.0, "stamp_tax": 0.0, "slippage": 0.0, "realized_pnl": 0.0, "fills": []})["end_equity"] = float(point["value"])
        for fill in fills:
            day = fill.datetime.date().isoformat()
            row = rows.setdefault(day, {"date": day, "buy_count": 0, "sell_count": 0, "buy_amount": 0.0, "sell_amount": 0.0,
                                        "commission": 0.0, "stamp_tax": 0.0, "slippage": 0.0, "realized_pnl": 0.0, "fills": []})
            amount = fill.price * fill.volume
            if fill.direction == Direction.LONG:
                row["buy_count"] += 1; row["buy_amount"] += amount
            else:
                row["sell_count"] += 1; row["sell_amount"] += amount
            row["commission"] += fill.commission; row["stamp_tax"] += fill.stamp_tax; row["slippage"] += fill.slippage
            row["fills"].append(VnpyMinuteBacktestService._portfolio_fill_to_trade(fill, instrument_names))
        for trade in completed_trades:
            rows.setdefault(str(trade["exit_date"])[:10], {"date": str(trade["exit_date"])[:10], "buy_count": 0, "sell_count": 0, "buy_amount": 0.0, "sell_amount": 0.0,
                                                               "commission": 0.0, "stamp_tax": 0.0, "slippage": 0.0, "realized_pnl": 0.0, "fills": []})["realized_pnl"] += float(trade["pnl_amount"] or 0)
        previous_equity = float(initial_capital)
        output = []
        for day, row in sorted(rows.items()):
            equity = row.get("end_equity")
            row["daily_return"] = round(equity / previous_equity - 1, 8) if equity is not None and previous_equity else None
            if equity is not None:
                previous_equity = equity
            for key in ("buy_amount", "sell_amount", "commission", "stamp_tax", "slippage", "realized_pnl"):
                row[key] = round(row[key], 2)
            output.append(row)
        return output

    @staticmethod
    def _portfolio_signal_to_dict(signal, instrument_names: dict[str, str]) -> dict:
        return {"id": signal.id, "symbol": signal.symbol, "name": instrument_names.get(signal.symbol) or None,
                "direction": VnpyMinuteBacktestService._direction_code(signal.direction), "timestamp": signal.datetime.isoformat(sep=" "), "reason": signal.reason,
                "conditions": list(signal.diagnostic.get("matched_conditions", [])), "diagnostic": signal.diagnostic,
                "status": signal.status, "due_at": signal.due_at.isoformat(sep=" ") if signal.due_at else None,
                "fill_datetime": signal.fill_datetime.isoformat(sep=" ") if signal.fill_datetime else None,
                "rejection_reason": signal.rejection_reason}

    @staticmethod
    def _direction_code(direction: Direction) -> str:
        """Keep the API transport contract stable instead of exposing vn.py's Chinese enum values."""
        return "LONG" if direction == Direction.LONG else "SHORT"

    @staticmethod
    def _portfolio_metrics(*, initial_capital: float, equity_curve: list[dict], completed_trades: list[dict], fills, trading_days: int) -> tuple[dict, list[dict]]:
        """Compute portfolio statistics from local, minute-marked equity only."""
        peak = float(initial_capital)
        max_drawdown = 0.0
        drawdown_curve: list[dict] = []
        daily_values: dict[str, float] = {}
        for point in equity_curve:
            value = float(point["value"])
            peak = max(peak, value)
            drawdown = value / peak - 1 if peak else 0.0
            max_drawdown = min(max_drawdown, drawdown)
            drawdown_curve.append({"date": point["date"], "value": round(drawdown, 8)})
            daily_values[str(point["date"])[:10]] = value

        end_balance = float(equity_curve[-1]["value"]) if equity_curve else float(initial_capital)
        total_return = end_balance / initial_capital - 1 if initial_capital else 0.0
        annual_return = (
            (end_balance / initial_capital) ** (252 / trading_days) - 1
            if initial_capital > 0 and end_balance > 0 and trading_days > 0
            else None
        )
        values = [initial_capital, *daily_values.values()]
        daily_returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]
        sharpe = None
        if len(daily_returns) >= 2:
            volatility = stdev(daily_returns)
            if volatility > 0:
                sharpe = (sum(daily_returns) / len(daily_returns)) / volatility * (252 ** 0.5)

        pnls = [float(trade["pnl_amount"] or 0) for trade in completed_trades]
        returns = [float(trade["pnl_pct"]) for trade in completed_trades if trade["pnl_pct"] is not None]
        gains = sum(value for value in pnls if value > 0)
        losses = -sum(value for value in pnls if value < 0)
        commissions = sum(float(fill.commission) for fill in fills)
        stamp_tax = sum(float(fill.stamp_tax) for fill in fills)
        slippage = sum(float(fill.slippage) for fill in fills)
        return {
            "total_trade_count": len(completed_trades),
            "n_trades": len(completed_trades),
            "order_fill_count": len(fills),
            "total_return": round(total_return, 8),
            "annual_return": round(annual_return, 8) if annual_return is not None else None,
            "end_balance": round(end_balance, 2),
            "final_equity": round(end_balance, 2),
            "total_pnl": round(end_balance - initial_capital, 2),
            "max_drawdown": round(max_drawdown, 8),
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "win_rate": round(sum(1 for value in pnls if value > 0) / len(pnls), 6) if pnls else None,
            "profit_factor": round(gains / losses, 4) if losses > 0 else None,
            "avg_trade_return": round(sum(returns) / len(returns), 8) if returns else None,
            "commission": round(commissions, 2),
            "stamp_tax": round(stamp_tax, 2),
            "slippage_cost": round(slippage, 2),
            "total_cost": round(commissions + stamp_tax + slippage, 2),
        }, drawdown_curve

    @staticmethod
    def _result_config(config: VnpyMinuteBacktestConfig, spec: StrategySpec, symbols: list[str], settings: dict) -> dict:
        return {"engine": "vnpy", "frequency": "1m", "strategy_id": spec.id, "symbols": symbols,
                "start": str(config.start), "end": str(config.end), "initial_capital": config.initial_capital,
                "max_positions": config.max_positions, "max_buy_volume_ratio": config.max_buy_volume_ratio,
                "max_sell_volume_ratio": config.max_sell_volume_ratio, "candidate_sort": config.candidate_sort,
                "entry_fill": config.entry_fill, "exit_fill": config.exit_fill,
                "force_close_at_end": config.force_close_at_end, "params": settings}
