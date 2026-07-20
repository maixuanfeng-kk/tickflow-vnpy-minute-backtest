"""Pure rules for the opening-volume minute portfolio strategy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from math import floor
from typing import TypedDict
from uuid import uuid4


VOLUME_RATIO_MIN = 1.5
INITIAL_CAPITAL = 10_000_000.0
MAX_POSITIONS = 8
LOT_SIZE = 100
TARGET_POSITION_VALUE = INITIAL_CAPITAL / MAX_POSITIONS


class Candidate(TypedDict):
    symbol: str
    volume_ratio: float
    today_return: float


def entry_reason(
    *,
    previous_open: float,
    previous_close: float,
    previous_change_pct: float,
    today_return: float,
    volume_ratio: float,
    crossed_previous_high: bool,
) -> str | None:
    """Return the first configured entry branch satisfied by a minute bar."""
    if volume_ratio < VOLUME_RATIO_MIN:
        return None
    if previous_close < previous_open and crossed_previous_high:
        return "previous_bearish_breakout"
    if 0.03 < today_return < 0.05 and previous_change_pct < 0.05:
        return "two_day_moderate_rise"
    if previous_close > previous_open and previous_change_pct < 0.05:
        return "previous_moderate_rise"
    return None


def rank_candidates(rows: list[Candidate]) -> list[Candidate]:
    """Use the agreed deterministic cross-symbol candidate ordering."""
    return sorted(
        rows,
        key=lambda row: (-row["volume_ratio"], -row["today_return"], row["symbol"]),
    )


@dataclass(frozen=True)
class MinutePortfolioConfig:
    symbols: list[str]
    start: date | None = None
    end: date | None = None
    initial_capital: float = INITIAL_CAPITAL
    max_positions: int = MAX_POSITIONS
    lot_size: int = LOT_SIZE
    commission_pct: float = 0.0002
    stamp_tax_pct: float = 0.001
    slippage_bps: float = 5.0


class MinutePortfolioEngine:
    """Small deterministic multi-symbol, next-minute-open portfolio simulator."""

    def __init__(self, config: MinutePortfolioConfig) -> None:
        self.config = config

    def run(self, rows: list[dict], daily_context: dict[tuple[str, date], dict]) -> dict:
        grouped: dict[datetime, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["datetime"], []).append(row)

        cash = self.config.initial_capital
        positions: dict[str, dict] = {}
        pending_buys: list[dict] = []
        entered_today: set[tuple[str, date]] = set()
        intraday_high: dict[tuple[str, date], float] = {}
        trades: list[dict] = []

        for timestamp in sorted(grouped):
            bars = {row["symbol"]: row for row in grouped[timestamp]}
            for order in pending_buys[:]:
                bar = bars.get(order["symbol"])
                if bar is None or float(bar["open"]) <= 0 or float(bar["volume"]) <= 0:
                    continue
                price = float(bar["open"]) * (1 + self.config.slippage_bps / 10_000)
                target = self.config.initial_capital / self.config.max_positions
                shares = floor(min(target, cash) / (price * (1 + self.config.commission_pct)))
                shares = shares // self.config.lot_size * self.config.lot_size
                pending_buys.remove(order)
                if shares < self.config.lot_size or len(positions) >= self.config.max_positions:
                    continue
                cost = shares * price * (1 + self.config.commission_pct)
                cash -= cost
                positions[order["symbol"]] = {
                    "shares": shares,
                    "entry_price": price,
                    "entry_datetime": timestamp,
                    "entry_reason": order["reason"],
                }

            candidates: list[Candidate] = []
            for symbol, bar in bars.items():
                current_date = timestamp.date()
                key = (symbol, current_date)
                prior_high = intraday_high.get(key, float("-inf"))
                intraday_high[key] = max(prior_high, float(bar["high"]))
                context = daily_context.get(key)
                if context is None or symbol in positions or key in entered_today:
                    continue
                current_time = timestamp.time()
                if not time(9, 30) <= current_time <= time(9, 59):
                    continue
                previous_volume = float(bar.get("previous_cumulative_volume") or 0)
                if previous_volume <= 0:
                    continue
                cumulative_volume = float(bar.get("cumulative_volume", bar["volume"]))
                volume_ratio = cumulative_volume / previous_volume
                today_return = float(bar["close"]) / float(context["previous_close"]) - 1
                reason = entry_reason(
                    previous_open=float(context["previous_open"]),
                    previous_close=float(context["previous_close"]),
                    previous_change_pct=float(context["previous_change_pct"]),
                    today_return=today_return,
                    volume_ratio=volume_ratio,
                    crossed_previous_high=(
                        prior_high < float(context["previous_high"])
                        and float(bar["high"]) >= float(context["previous_high"])
                    ),
                )
                if reason is not None:
                    candidates.append({
                        "symbol": symbol,
                        "volume_ratio": volume_ratio,
                        "today_return": today_return,
                        "reason": reason,
                    })

            slots = self.config.max_positions - len(positions) - len(pending_buys)
            for candidate in rank_candidates(candidates)[:max(slots, 0)]:
                entered_today.add((candidate["symbol"], timestamp.date()))
                pending_buys.append(candidate)

        for symbol, position in positions.items():
            trades.append({
                "symbol": symbol,
                "entry_datetime": position["entry_datetime"].isoformat(sep=" "),
                "entry_price": round(position["entry_price"], 4),
                "shares": position["shares"],
                "entry_reason": position["entry_reason"],
            })
        return {"cash": cash, "trades": trades}


class MinutePortfolioService:
    """Load TickFlow K lines and adapt the native engine to the backtest result contract."""

    def __init__(self, repo) -> None:
        self.repo = repo

    def run(self, config: MinutePortfolioConfig) -> dict:
        if config.start is None or config.end is None:
            raise ValueError("start and end are required")
        daily = self.repo.get_daily_batch(
            config.symbols,
            config.start - timedelta(days=20),
            config.end,
            columns=["symbol", "date", "open", "high", "close", "ma5"],
        )
        minutes = self.repo.get_minute_range(
            config.symbols, config.start - timedelta(days=7), config.end, asset_type="stock",
        )
        if minutes.is_empty():
            raise ValueError("no minute bars in the selected range")

        history: dict[str, list[dict]] = {}
        for row in daily.sort(["symbol", "date"]).to_dicts():
            history.setdefault(row["symbol"], []).append(row)
        contexts: dict[tuple[str, date], dict] = {}
        for symbol, rows in history.items():
            for current in rows:
                prior = [row for row in rows if row["date"] < current["date"]]
                if prior:
                    previous = prior[-1]
                    earlier = prior[-2] if len(prior) > 1 else None
                    previous_close = float(previous["close"])
                    change = (
                        previous_close / float(earlier["close"]) - 1
                        if earlier and float(earlier["close"]) > 0 else 0.0
                    )
                    contexts[(symbol, current["date"])] = {
                        "previous_open": previous["open"],
                        "previous_close": previous_close,
                        "previous_high": previous["high"],
                        "previous_change_pct": change,
                        "previous_ma5": previous.get("ma5"),
                    }

        raw_rows = minutes.sort(["symbol", "datetime"]).to_dicts()
        cumulative: dict[tuple[str, date, time], float] = {}
        running: dict[tuple[str, date], float] = {}
        for row in raw_rows:
            key = (row["symbol"], row["datetime"].date())
            running[key] = running.get(key, 0.0) + float(row["volume"] or 0)
            row["cumulative_volume"] = running[key]
            cumulative[(row["symbol"], key[1], row["datetime"].time())] = running[key]
        trade_dates = sorted({row["datetime"].date() for row in raw_rows})
        previous_date = {trade_dates[index]: trade_dates[index - 1] for index in range(1, len(trade_dates))}
        for row in raw_rows:
            prior_day = previous_date.get(row["datetime"].date())
            row["previous_cumulative_volume"] = (
                cumulative.get((row["symbol"], prior_day, row["datetime"].time()), 0.0)
                if prior_day else 0.0
            )

        executed = MinutePortfolioEngine(config).run(raw_rows, contexts)
        return {
            "run_id": uuid4().hex,
            "config": {"engine": "minute_portfolio", "frequency": "1m", "symbols": config.symbols,
                       "initial_capital": config.initial_capital},
            "stats": {"total_trade_count": len(executed["trades"]), "end_balance": executed["cash"]},
            "equity_curve": [], "drawdown_curve": [], "benchmark_curve": [],
            "trades": executed["trades"], "per_symbol_stats": [],
            "strategy_info": {"id": "opening_volume_portfolio", "name": "早盘放量组合", "source": "native"},
        }
