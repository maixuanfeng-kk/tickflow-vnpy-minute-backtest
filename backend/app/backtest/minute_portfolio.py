"""Pure rules for the opening-volume minute portfolio strategy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import floor
from typing import Any, Mapping, TypedDict
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


@dataclass(frozen=True)
class OpeningVolumeStrategyParams:
    scan_start_time: time = time(9, 30)
    scan_end_time: time = time(9, 59)
    volume_multiple: float = VOLUME_RATIO_MIN
    enable_branch_a: bool = True
    enable_branch_b: bool = True
    enable_branch_c: bool = True
    stop_loss_pct: float = 0.02
    ma_exit_period: int = 5

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "OpeningVolumeStrategyParams":
        values = values or {}

        def parse_time(value: Any, field_name: str, default: time) -> time:
            if value is None:
                return default
            if isinstance(value, time):
                return value
            if isinstance(value, str):
                try:
                    return datetime.strptime(value, "%H:%M").time()
                except ValueError as exc:
                    raise ValueError(f"{field_name} must use HH:MM format") from exc
            raise ValueError(f"{field_name} must use HH:MM format")

        def parse_bool(value: Any, field_name: str, default: bool) -> bool:
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in {"true", "false"}:
                return value.lower() == "true"
            raise ValueError(f"{field_name} must be boolean")

        start = parse_time(values.get("scan_start_time"), "scan_start_time", cls.scan_start_time)
        end = parse_time(values.get("scan_end_time"), "scan_end_time", cls.scan_end_time)
        if start > end:
            raise ValueError("scan_start_time must not be after scan_end_time")
        volume_multiple = float(values.get("volume_multiple", cls.volume_multiple))
        stop_loss_pct = float(values.get("stop_loss_pct", cls.stop_loss_pct))
        ma_exit_period = int(values.get("ma_exit_period", cls.ma_exit_period))
        if volume_multiple <= 0:
            raise ValueError("volume_multiple must be positive")
        if stop_loss_pct < 0:
            raise ValueError("stop_loss_pct must not be negative")
        if ma_exit_period < 1:
            raise ValueError("ma_exit_period must be positive")
        return cls(
            scan_start_time=start,
            scan_end_time=end,
            volume_multiple=volume_multiple,
            enable_branch_a=parse_bool(values.get("enable_branch_a"), "enable_branch_a", cls.enable_branch_a),
            enable_branch_b=parse_bool(values.get("enable_branch_b"), "enable_branch_b", cls.enable_branch_b),
            enable_branch_c=parse_bool(values.get("enable_branch_c"), "enable_branch_c", cls.enable_branch_c),
            stop_loss_pct=stop_loss_pct,
            ma_exit_period=ma_exit_period,
        )


def is_in_scan_window(current_time: time, params: OpeningVolumeStrategyParams) -> bool:
    return params.scan_start_time <= current_time <= params.scan_end_time


def entry_reason(
    *,
    previous_open: float,
    previous_close: float,
    previous_change_pct: float,
    today_return: float,
    volume_ratio: float,
    crossed_previous_high: bool,
    params: OpeningVolumeStrategyParams | None = None,
) -> str | None:
    """Return the first configured entry branch satisfied by a minute bar."""
    params = params or OpeningVolumeStrategyParams()
    if volume_ratio < params.volume_multiple:
        return None
    if params.enable_branch_a and previous_close < previous_open and crossed_previous_high:
        return "previous_bearish_breakout"
    if params.enable_branch_b and 0.03 < today_return < 0.05 and previous_change_pct < 0.05:
        return "two_day_moderate_rise"
    if params.enable_branch_c and previous_close > previous_open and previous_change_pct < 0.05:
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
    strategy_params: OpeningVolumeStrategyParams = field(default_factory=OpeningVolumeStrategyParams)


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
        pending_sells: set[str] = set()
        entered_today: set[tuple[str, date]] = set()
        intraday_high: dict[tuple[str, date], float] = {}
        trades: list[dict] = []

        for timestamp in sorted(grouped):
            bars = {row["symbol"]: row for row in grouped[timestamp]}
            for symbol in list(pending_sells):
                bar = bars.get(symbol)
                position = positions.get(symbol)
                if bar is None or position is None or float(bar["open"]) <= 0:
                    continue
                price = float(bar["open"]) * (1 - self.config.slippage_bps / 10_000)
                value = position["shares"] * price
                cash += value * (1 - self.config.commission_pct - self.config.stamp_tax_pct)
                trades.append({
                    "symbol": symbol,
                    "entry_datetime": position["entry_datetime"].isoformat(sep=" "),
                    "entry_price": round(position["entry_price"], 4),
                    "exit_datetime": timestamp.isoformat(sep=" "),
                    "exit_price": round(price, 4),
                    "shares": position["shares"],
                    "entry_reason": position["entry_reason"],
                    "exit_reason": position["exit_reason"],
                })
                pending_sells.remove(symbol)
                positions.pop(symbol)
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
                    "entry_date": timestamp.date(),
                    "entry_reason": order["reason"],
                }

            for symbol, position in positions.items():
                if position["entry_date"] >= timestamp.date() or symbol in pending_sells:
                    continue
                context = daily_context.get((symbol, timestamp.date()))
                close = float(bars.get(symbol, {}).get("close", 0) or 0)
                ma_key = f"previous_ma{self.config.strategy_params.ma_exit_period}"
                ma_value = float(context.get(ma_key) or 0) if context else 0
                if close > 0 and close <= position["entry_price"] * (1 - self.config.strategy_params.stop_loss_pct):
                    position["exit_reason"] = "stop_loss"
                    pending_sells.add(symbol)
                elif close > 0 and ma_value > 0 and close < ma_value:
                    position["exit_reason"] = f"ma{self.config.strategy_params.ma_exit_period}_breakdown"
                    pending_sells.add(symbol)

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
                if not is_in_scan_window(current_time, self.config.strategy_params):
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
                    params=self.config.strategy_params,
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
        ma_column = f"ma{config.strategy_params.ma_exit_period}"
        daily = self.repo.get_daily_batch(
            config.symbols,
            config.start - timedelta(days=20),
            config.end,
            columns=["symbol", "date", "open", "high", "close", ma_column],
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
                        f"previous_ma{config.strategy_params.ma_exit_period}": previous.get(ma_column),
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
                       "initial_capital": config.initial_capital, "max_positions": config.max_positions},
            "stats": {"total_trade_count": len(executed["trades"]), "end_balance": executed["cash"]},
            "equity_curve": [], "drawdown_curve": [], "benchmark_curve": [],
            "trades": executed["trades"], "per_symbol_stats": [],
            "strategy_info": {"id": "opening_volume_portfolio", "name": "早盘放量组合", "source": "native"},
        }
