"""Pure rules for the opening-volume minute portfolio strategy."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from math import floor, isfinite
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping, TypedDict
from uuid import uuid4

import numpy as np
import polars as pl

from app.services import watchlist


VOLUME_RATIO_MIN = 1.5
CANDLE_DIRECTIONS = {"bearish", "bullish", "any"}
CANDIDATE_SORT_MODES = {"score", "volume_ratio", "watchlist_order"}
MA_EXIT_PERIODS = (5, 10, 20, 30, 60)
INITIAL_CAPITAL = 10_000_000.0
MAX_POSITIONS = 8
LOT_SIZE = 100
TARGET_POSITION_VALUE = INITIAL_CAPITAL / MAX_POSITIONS


class Candidate(TypedDict, total=False):
    symbol: str
    volume_ratio: float
    today_return: float
    previous_return: float
    cumulative_amount: float
    reason: str
    score: float


@dataclass(frozen=True)
class OpeningVolumeStrategyParams:
    scan_start_time: time = time(9, 30)
    scan_end_time: time = time(9, 59)
    volume_multiple: float | None = None
    enable_branch_a: bool = True
    branch_a_volume_multiple: float | None = None
    branch_a_previous_candle: str = "bearish"
    enable_branch_b: bool = True
    branch_b_volume_multiple: float | None = None
    branch_b_today_return_min: float = 0.03
    branch_b_today_return_max: float = 0.05
    branch_b_previous_return_max: float = 0.05
    enable_branch_c: bool = True
    branch_c_volume_multiple: float | None = None
    branch_c_previous_candle: str = "bullish"
    branch_c_previous_return_max: float = 0.05
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

        def parse_candle_direction(value: Any, field_name: str, default: str) -> str:
            if value is None:
                return default
            aliases = {
                "bearish": "bearish",
                "bullish": "bullish",
                "any": "any",
                "阴线": "bearish",
                "阳线": "bullish",
                "不限": "any",
            }
            parsed = aliases.get(str(value))
            if parsed not in CANDLE_DIRECTIONS:
                raise ValueError(f"{field_name} must be bearish, bullish, or any")
            return parsed

        def parse_optional_positive_float(value: Any, field_name: str) -> float | None:
            if value is None:
                return None
            parsed = float(value)
            if parsed <= 0:
                raise ValueError(f"{field_name} must be positive")
            return parsed

        start = parse_time(values.get("scan_start_time"), "scan_start_time", cls.scan_start_time)
        end = parse_time(values.get("scan_end_time"), "scan_end_time", cls.scan_end_time)
        if start > end:
            raise ValueError("scan_start_time must not be after scan_end_time")
        volume_multiple = parse_optional_positive_float(
            values.get("volume_multiple"), "volume_multiple",
        )
        branch_a_volume_multiple = parse_optional_positive_float(
            values.get("branch_a_volume_multiple"), "branch_a_volume_multiple",
        )
        branch_b_volume_multiple = parse_optional_positive_float(
            values.get("branch_b_volume_multiple"), "branch_b_volume_multiple",
        )
        branch_c_volume_multiple = parse_optional_positive_float(
            values.get("branch_c_volume_multiple"), "branch_c_volume_multiple",
        )
        branch_b_today_return_min = float(values.get(
            "branch_b_today_return_min", cls.branch_b_today_return_min,
        ))
        branch_b_today_return_max = float(values.get(
            "branch_b_today_return_max", cls.branch_b_today_return_max,
        ))
        branch_b_previous_return_max = float(values.get(
            "branch_b_previous_return_max", cls.branch_b_previous_return_max,
        ))
        branch_c_previous_return_max = float(values.get(
            "branch_c_previous_return_max", cls.branch_c_previous_return_max,
        ))
        stop_loss_pct = float(values.get("stop_loss_pct", cls.stop_loss_pct))
        ma_exit_period = int(values.get("ma_exit_period", cls.ma_exit_period))
        if branch_b_today_return_min >= branch_b_today_return_max:
            raise ValueError("branch_b_today_return_min must be below branch_b_today_return_max")
        if stop_loss_pct < 0:
            raise ValueError("stop_loss_pct must not be negative")
        if ma_exit_period not in MA_EXIT_PERIODS:
            raise ValueError(f"ma_exit_period must be one of {MA_EXIT_PERIODS}")
        return cls(
            scan_start_time=start,
            scan_end_time=end,
            volume_multiple=volume_multiple,
            enable_branch_a=parse_bool(values.get("enable_branch_a"), "enable_branch_a", cls.enable_branch_a),
            branch_a_volume_multiple=branch_a_volume_multiple,
            branch_a_previous_candle=parse_candle_direction(
                values.get("branch_a_previous_candle"),
                "branch_a_previous_candle",
                cls.branch_a_previous_candle,
            ),
            enable_branch_b=parse_bool(values.get("enable_branch_b"), "enable_branch_b", cls.enable_branch_b),
            branch_b_volume_multiple=branch_b_volume_multiple,
            branch_b_today_return_min=branch_b_today_return_min,
            branch_b_today_return_max=branch_b_today_return_max,
            branch_b_previous_return_max=branch_b_previous_return_max,
            enable_branch_c=parse_bool(values.get("enable_branch_c"), "enable_branch_c", cls.enable_branch_c),
            branch_c_volume_multiple=branch_c_volume_multiple,
            branch_c_previous_candle=parse_candle_direction(
                values.get("branch_c_previous_candle"),
                "branch_c_previous_candle",
                cls.branch_c_previous_candle,
            ),
            branch_c_previous_return_max=branch_c_previous_return_max,
            stop_loss_pct=stop_loss_pct,
            ma_exit_period=ma_exit_period,
        )


def is_in_scan_window(current_time: time, params: OpeningVolumeStrategyParams) -> bool:
    return params.scan_start_time <= current_time <= params.scan_end_time


def _matches_candle_direction(direction: str, previous_open: float, previous_close: float) -> bool:
    if direction == "any":
        return True
    if direction == "bearish":
        return previous_close < previous_open
    return previous_close > previous_open


def _passes_volume_filter(
    *,
    branch_multiple: float | None,
    legacy_multiple: float | None,
    volume_ratio: float,
) -> bool:
    required_multiple = branch_multiple or legacy_multiple or VOLUME_RATIO_MIN
    return volume_ratio >= required_multiple


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
    if (
        params.enable_branch_a
        and _passes_volume_filter(
            branch_multiple=params.branch_a_volume_multiple,
            legacy_multiple=params.volume_multiple,
            volume_ratio=volume_ratio,
        )
        and _matches_candle_direction(
            params.branch_a_previous_candle, previous_open, previous_close,
        )
        and crossed_previous_high
    ):
        return "previous_bearish_breakout"
    if (
        params.enable_branch_b
        and _passes_volume_filter(
            branch_multiple=params.branch_b_volume_multiple,
            legacy_multiple=params.volume_multiple,
            volume_ratio=volume_ratio,
        )
        and params.branch_b_today_return_min < today_return < params.branch_b_today_return_max
        and previous_change_pct < params.branch_b_previous_return_max
    ):
        return "two_day_moderate_rise"
    if (
        params.enable_branch_c
        and _passes_volume_filter(
            branch_multiple=params.branch_c_volume_multiple,
            legacy_multiple=params.volume_multiple,
            volume_ratio=volume_ratio,
        )
        and _matches_candle_direction(
            params.branch_c_previous_candle, previous_open, previous_close,
        )
        and previous_change_pct < params.branch_c_previous_return_max
    ):
        return "previous_moderate_rise"
    return None


def rank_candidates(
    rows: list[Candidate],
    *,
    mode: str = "volume_ratio",
    weights: Mapping[str, float] | None = None,
    score_min: float | None = None,
    score_max: float | None = None,
    watchlist_order: list[str] | None = None,
) -> list[Candidate]:
    """Use the selected deterministic cross-symbol candidate ordering."""
    if mode == "score":
        return _score_candidates(rows, weights or {}, score_min, score_max)
    if mode == "volume_ratio":
        return sorted(
            rows,
            key=lambda row: (-row["volume_ratio"], -row["today_return"], row["symbol"]),
        )
    if mode == "watchlist_order":
        order = {
            symbol: index
            for index, symbol in reversed(list(enumerate(watchlist_order or [])))
        }
        fallback_rank = max(order.values(), default=-1) + 1
        return sorted(rows, key=lambda row: (order.get(row["symbol"], fallback_rank), row["symbol"]))
    raise ValueError(f"candidate_sort must be one of {sorted(CANDIDATE_SORT_MODES)}")


def _symbol_board(symbol: str) -> str | None:
    code = symbol.split(".", 1)[0]
    if code.startswith("688"):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("4", "8", "92")):
        return "北交所"
    if code.startswith("6"):
        return "沪主板"
    if code.startswith(("0", "1")):
        return "深主板"
    return None


def _passes_basic_filter(
    symbol: str,
    bar: Mapping[str, Any],
    basic_filter: Mapping[str, Any],
    symbol_names: Mapping[str, str],
) -> bool:
    if not basic_filter or basic_filter.get("enabled", True) is False:
        return True
    close = float(bar.get("close") or 0)
    cumulative_amount = float(bar.get("cumulative_amount", bar.get("amount") or 0))
    for key, value, actual, lower in (
        ("price_min", basic_filter.get("price_min"), close, True),
        ("price_max", basic_filter.get("price_max"), close, False),
        ("amount_min", basic_filter.get("amount_min"), cumulative_amount, True),
        ("amount_max", basic_filter.get("amount_max"), cumulative_amount, False),
    ):
        if value is None:
            continue
        threshold = float(value)
        if (lower and actual < threshold) or (not lower and actual > threshold):
            return False
    boards = basic_filter.get("boards")
    if boards and _symbol_board(symbol) not in boards:
        return False
    if basic_filter.get("exclude_st"):
        name = str(symbol_names.get(symbol, "")).upper()
        if name.startswith(("ST", "*ST")) or "退" in name:
            return False
    return True


def _score_candidates(
    rows: list[Candidate],
    weights: Mapping[str, float],
    score_min: float | None,
    score_max: float | None,
) -> list[Candidate]:
    if not rows:
        return []
    active_weights = {
        key: float(weight)
        for key, weight in weights.items()
        if key in {"volume_ratio", "today_return", "previous_return"}
        and isfinite(float(weight)) and float(weight) > 0
    }
    if not active_weights:
        return rank_candidates(rows)
    total_weight = sum(active_weights.values())
    ranges = {
        key: (
            min(float(row.get(key, 0.0)) for row in rows),
            max(float(row.get(key, 0.0)) for row in rows),
        )
        for key in active_weights
    }
    scored: list[Candidate] = []
    for row in rows:
        score = 0.0
        for key, weight in active_weights.items():
            low, high = ranges[key]
            normalized = (
                (float(row.get(key, 0.0)) - low) / (high - low) * 100
                if high > low else 0.0
            )
            score += normalized * weight / total_weight
        candidate = Candidate(**row, score=score)
        if score_min is not None and score < score_min:
            continue
        if score_max is not None and score > score_max:
            continue
        scored.append(candidate)
    return sorted(
        scored,
        key=lambda row: (
            -row.get("score", 0.0),
            -row["volume_ratio"],
            -row["today_return"],
            row["symbol"],
        ),
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
    cash_reserve_ratio: float = 0.0
    max_buy_volume_ratio: float | None = None
    strategy_params: OpeningVolumeStrategyParams = field(default_factory=OpeningVolumeStrategyParams)
    basic_filter: dict[str, Any] = field(default_factory=dict)
    candidate_sort: str = "volume_ratio"
    entry_fill: str = "next_minute_open"
    exit_fill: str = "next_minute_open"
    force_close_at_end: bool = True
    scoring: dict[str, float] = field(default_factory=dict)
    score_min: float | None = None
    score_max: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    trailing_take_profit_activate_pct: float | None = None
    trailing_take_profit_drawdown_pct: float | None = None
    max_hold_days: int | None = None
    symbol_names: dict[str, str] = field(default_factory=dict)
    minute_data_dir: str | None = None


def _entry_base_price(bar: Mapping[str, Any], fill: str) -> float:
    key = "close" if fill == "signal_minute_close" else "open"
    return float(bar.get(key) or 0)


def _exit_base_price(bar: Mapping[str, Any], fill: str) -> float:
    key = "close" if fill == "signal_minute_close" else "open"
    return float(bar.get(key) or 0)


def _price_limit_ratio(symbol: str, name: str) -> float:
    if str(name or "").upper().startswith(("ST", "*ST")):
        return 0.05
    board = _symbol_board(symbol)
    if board in {"科创板", "创业板"}:
        return 0.20
    if board == "北交所":
        return 0.30
    return 0.10


def _is_one_price_limit(
    symbol: str,
    bar: Mapping[str, Any],
    previous_close: float,
    name: str,
    direction: str,
) -> bool:
    prices = [float(bar.get(key) or 0) for key in ("open", "high", "low", "close")]
    if previous_close <= 0 or any(price <= 0 for price in prices):
        return False
    tolerance = max(abs(prices[-1]) * 1e-4, 0.01)
    if max(prices) - min(prices) > tolerance:
        return False
    ratio = _price_limit_ratio(symbol, name)
    multiplier = Decimal(str(1 + ratio if direction == "up" else 1 - ratio))
    limit_price = float(
        (Decimal(str(previous_close)) * multiplier).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )
    return abs(prices[-1] - limit_price) <= 0.01


def _buy_block_reason(
    *,
    symbol: str,
    bar: Mapping[str, Any] | None,
    previous_close: float,
    symbol_name: str,
    fill: str,
) -> str | None:
    if bar is None or float(bar.get("volume") or 0) <= 0:
        return "buy_suspended"
    if _entry_base_price(bar, fill) <= 0:
        return "buy_invalid_price"
    if _is_one_price_limit(symbol, bar, previous_close, symbol_name, "up"):
        return "buy_limit_up"
    return None


def _sell_block_reason(
    *,
    symbol: str,
    bar: Mapping[str, Any] | None,
    previous_close: float,
    symbol_name: str,
    fill: str,
) -> str | None:
    if bar is None or float(bar.get("volume") or 0) <= 0:
        return "sell_suspended"
    if _exit_base_price(bar, fill) <= 0:
        return "sell_invalid_price"
    if _is_one_price_limit(symbol, bar, previous_close, symbol_name, "down"):
        return "sell_limit_down"
    return None


class LocalMinuteParquetRepository:
    """Read pytdx per-symbol minute Parquet files through the repository contract."""

    def __init__(
        self,
        data_dir: str | Path,
        progress_callback: Callable[[int, int], None] | None = None,
        progress_total: int = 0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.progress_callback = progress_callback
        self.progress_total = progress_total
        self.progress_done = 0

    @staticmethod
    def _symbol(value: str) -> str:
        return value.replace(".XSHG", ".SH").replace(".XSHE", ".SZ")

    def _read(self, symbols: list[str], start: date, end: date) -> pl.DataFrame:
        frames: list[pl.DataFrame] = []
        for symbol in symbols:
            symbol = self._symbol(symbol)
            path = self.data_dir / f"{symbol}.parquet"
            if not path.exists():
                path = self.data_dir / f"{symbol.replace('.', '_')}.parquet"
            if not path.exists():
                self._advance_progress()
                continue
            frame = pl.read_parquet(path).select(["ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"])
            frames.append(frame.rename({"ts_code": "symbol", "trade_time": "datetime", "vol": "volume"}).with_columns(
                pl.col("symbol").str.replace_all(".XSHG", ".SH", literal=True).str.replace_all(".XSHE", ".SZ", literal=True),
                pl.col("datetime").cast(pl.Utf8).str.strptime(pl.Datetime, strict=False),
            ).filter(pl.col("datetime").dt.date().is_between(start, end)))
            self._advance_progress()
        if not frames:
            return pl.DataFrame(schema={"symbol": pl.String, "datetime": pl.Datetime, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64})
        return pl.concat(frames).select(["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"])

    def _advance_progress(self) -> None:
        self.progress_done += 1
        if self.progress_callback and (
            self.progress_done % 5 == 0 or self.progress_done >= self.progress_total
        ):
            self.progress_callback(self.progress_done, self.progress_total)

    def get_minute_range(self, symbols: list[str], start: date, end: date, asset_type: str = "stock") -> pl.DataFrame:
        return self._read(symbols, start, end)

    def get_daily_batch(self, symbols: list[str], start: date, end: date, columns: list[str]) -> pl.DataFrame:
        minutes = self._read(symbols, start, end)
        if minutes.is_empty():
            return pl.DataFrame(schema={column: pl.Float64 for column in columns})
        daily = minutes.filter(
            pl.col("datetime").dt.time() >= time(9, 25)
        ).sort(["symbol", "datetime"]).with_columns(
            pl.col("datetime").dt.date().alias("date")
        ).group_by(["symbol", "date"], maintain_order=True).agg(
            pl.col("open").first(), pl.col("high").max(), pl.col("low").min(),
            pl.col("close").last(), pl.col("volume").sum(),
        ).sort(["symbol", "date"])
        for period in MA_EXIT_PERIODS:
            daily = daily.with_columns(pl.col("close").rolling_mean(period).over("symbol").alias(f"ma{period}"))
        return daily.select([column for column in columns if column in daily.columns])


@dataclass(frozen=True)
class OpeningVolumeScanConfig:
    as_of: date
    strategy_params: OpeningVolumeStrategyParams


def _load_rows_and_context(
    repo,
    symbols: list[str],
    start: date,
    end: date,
    ma_exit_period: int,
) -> tuple[list[dict], dict[tuple[str, date], dict]]:
    ma_column = f"ma{ma_exit_period}"
    daily = repo.get_daily_batch(
        symbols,
        start - timedelta(days=20),
        end,
        columns=["symbol", "date", "open", "high", "close", ma_column],
    )
    minutes = repo.get_minute_range(
        symbols, start - timedelta(days=7), end, asset_type="stock",
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
                    "previous_closes": [float(row["close"]) for row in prior[-4:]],
                    f"previous_ma{ma_exit_period}": previous.get(ma_column),
                }

    raw_rows = minutes.sort(["symbol", "datetime"]).to_dicts()
    cumulative: dict[tuple[str, date, time], float] = {}
    running: dict[tuple[str, date], float] = {}
    running_amount: dict[tuple[str, date], float] = {}
    for row in raw_rows:
        key = (row["symbol"], row["datetime"].date())
        running[key] = running.get(key, 0.0) + float(row["volume"] or 0)
        running_amount[key] = running_amount.get(key, 0.0) + float(row.get("amount") or 0)
        row["cumulative_volume"] = running[key]
        row["cumulative_amount"] = running_amount[key]
        cumulative[(row["symbol"], key[1], row["datetime"].time())] = running[key]
    trade_dates = sorted({row["datetime"].date() for row in raw_rows})
    previous_date = {trade_dates[index]: trade_dates[index - 1] for index in range(1, len(trade_dates))}
    for row in raw_rows:
        prior_day = previous_date.get(row["datetime"].date())
        row["previous_cumulative_volume"] = (
            cumulative.get((row["symbol"], prior_day, row["datetime"].time()), 0.0)
            if prior_day else 0.0
        )
    return raw_rows, contexts


class MinutePortfolioEngine:
    """Small deterministic multi-symbol, next-minute-open portfolio simulator."""

    def __init__(self, config: MinutePortfolioConfig) -> None:
        self.config = config

    def run(
        self,
        rows: list[dict],
        daily_context: dict[tuple[str, date], dict],
        progress_callback: Callable[[int, int, float, date], None] | None = None,
    ) -> dict:
        grouped: dict[datetime, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["datetime"], []).append(row)

        cash = self.config.initial_capital
        positions: dict[str, dict] = {}
        pending_buys: list[dict] = []
        pending_sells: dict[str, str] = {}
        entered_today: set[tuple[str, date]] = set()
        trades: list[dict] = []
        equity_curve: list[dict] = []
        drawdown_curve: list[dict] = []
        execution = {
            "buy_suspended": 0,
            "buy_invalid_price": 0,
            "buy_limit_up": 0,
            "sell_suspended": 0,
            "sell_invalid_price": 0,
            "sell_limit_down": 0,
        }
        latest_closes: dict[str, float] = {}
        latest_bars: dict[str, tuple[datetime, dict]] = {}
        last_sell_attempt: dict[str, datetime] = {}
        current_date: date | None = None
        peak_equity = self.config.initial_capital
        trade_dates = sorted({timestamp.date() for timestamp in grouped})
        trade_day_index = {trading_day: index for index, trading_day in enumerate(trade_dates)}

        def snapshot(day: date) -> None:
            nonlocal peak_equity
            equity = cash + sum(
                position["shares"] * latest_closes.get(symbol, position["entry_price"])
                for symbol, position in positions.items()
            )
            peak_equity = max(peak_equity, equity)
            drawdown = (equity - peak_equity) / peak_equity if peak_equity > 0 else 0.0
            equity_curve.append({
                "date": str(day), "value": round(equity, 2),
                "cash": round(cash, 2), "positions": len(positions),
            })
            drawdown_curve.append({"date": str(day), "value": round(drawdown, 6)})
            if progress_callback:
                progress_callback(len(equity_curve), len(trade_dates), equity, day)

        def close_position(
            symbol: str,
            position: dict,
            bar: Mapping[str, Any] | None,
            fill: str,
            execution_time: datetime,
        ) -> bool:
            nonlocal cash
            last_sell_attempt[symbol] = execution_time
            context = daily_context.get((symbol, execution_time.date())) or {}
            block_reason = _sell_block_reason(
                symbol=symbol,
                bar=bar,
                previous_close=float(context.get("previous_close") or 0),
                symbol_name=self.config.symbol_names.get(symbol, ""),
                fill=fill,
            )
            if block_reason is not None:
                execution[block_reason] += 1
                position["exit_block_reason"] = block_reason
                return False
            assert bar is not None
            base_price = _exit_base_price(bar, fill)
            price = base_price * (1 - self.config.slippage_bps / 10_000)
            value = position["shares"] * price
            net_proceeds = value * (
                1 - self.config.commission_pct - self.config.stamp_tax_pct
            )
            cash += net_proceeds
            pnl_amount = net_proceeds - position["entry_cost"]
            pnl_pct = pnl_amount / position["entry_cost"] if position["entry_cost"] else 0.0
            trades.append({
                "symbol": symbol,
                "entry_datetime": position["entry_datetime"].isoformat(sep=" "),
                "entry_date": str(position["entry_date"]),
                "entry_price": round(position["entry_price"], 4),
                "exit_datetime": execution_time.isoformat(sep=" "),
                "exit_date": str(execution_time.date()),
                "exit_price": round(price, 4),
                "shares": position["shares"],
                "entry_cost": round(position["entry_cost"], 2),
                "pnl_amount": round(pnl_amount, 2),
                "pnl_pct": round(pnl_pct, 6),
                "max_floating_gain_pct": round(position["max_floating_gain_pct"], 6),
                "max_floating_loss_pct": round(position["max_floating_loss_pct"], 6),
                "duration": (execution_time.date() - position["entry_date"]).days,
                "entry_reason": position["entry_reason"],
                "exit_reason": position["exit_reason"],
            })
            positions.pop(symbol)
            return True

        def open_position(
            order: Mapping[str, Any],
            bar: Mapping[str, Any] | None,
            fill: str,
            execution_time: datetime,
            bars: Mapping[str, Mapping[str, Any]],
        ) -> bool:
            nonlocal cash
            context = daily_context.get((order["symbol"], execution_time.date())) or {}
            block_reason = _buy_block_reason(
                symbol=order["symbol"],
                bar=bar,
                previous_close=float(context.get("previous_close") or 0),
                symbol_name=self.config.symbol_names.get(order["symbol"], ""),
                fill=fill,
            )
            if block_reason is not None:
                execution[block_reason] += 1
                return False
            assert bar is not None
            base_price = _entry_base_price(bar, fill)
            price = base_price * (1 + self.config.slippage_bps / 10_000)
            current_equity = cash
            for held_symbol, position in positions.items():
                held_bar = bars.get(held_symbol)
                mark_price = (
                    float(held_bar.get("open") or held_bar.get("close") or 0)
                    if held_bar else 0.0
                )
                if mark_price <= 0:
                    mark_price = float(
                        latest_closes.get(held_symbol) or position["entry_price"]
                    )
                current_equity += position["shares"] * mark_price
            reserve_cash = current_equity * self.config.cash_reserve_ratio
            target = (
                current_equity
                * (1 - self.config.cash_reserve_ratio)
                / self.config.max_positions
            )
            spendable_cash = max(cash - reserve_cash, 0.0)
            shares = floor(
                min(target, spendable_cash)
                / (price * (1 + self.config.commission_pct))
            )
            if self.config.max_buy_volume_ratio is not None:
                shares = min(
                    shares,
                    floor(float(bar["volume"]) * self.config.max_buy_volume_ratio),
                )
            shares = shares // self.config.lot_size * self.config.lot_size
            if shares < self.config.lot_size or len(positions) >= self.config.max_positions:
                return False
            cost = shares * price * (1 + self.config.commission_pct)
            cash -= cost
            positions[order["symbol"]] = {
                "shares": shares,
                "entry_price": price,
                "entry_datetime": execution_time,
                "entry_date": execution_time.date(),
                "entry_reason": order["reason"],
                "entry_cost": cost,
                "peak_price": price,
                "max_floating_gain_pct": 0.0,
                "max_floating_loss_pct": 0.0,
            }
            return True

        for timestamp in sorted(grouped):
            if current_date is not None and timestamp.date() != current_date:
                snapshot(current_date)
            current_date = timestamp.date()
            bars = {row["symbol"]: row for row in grouped[timestamp]}
            for symbol, bar in bars.items():
                latest_bars[symbol] = (timestamp, bar)
            for symbol in list(pending_sells):
                bar = bars.get(symbol)
                position = positions.get(symbol)
                if position is None:
                    continue
                pending_fill = pending_sells[symbol]
                if close_position(
                    symbol, position, bar, pending_fill, timestamp,
                ):
                    pending_sells.pop(symbol, None)
            for order in pending_buys[:]:
                execution_timestamp = order["execution_timestamp"]
                if execution_timestamp > timestamp:
                    continue
                pending_buys.remove(order)
                if execution_timestamp != timestamp:
                    execution["buy_suspended"] += 1
                    continue
                bar = bars.get(order["symbol"])
                if open_position(
                    order, bar, "next_minute_open", timestamp, bars,
                ):
                    entered_today.add((order["symbol"], timestamp.date()))

            for symbol, position in positions.items():
                bar = bars.get(symbol)
                if bar is None:
                    continue
                high = float(bar.get("high") or 0)
                low = float(bar.get("low") or 0)
                unit_cost = position["entry_cost"] / position["shares"]
                if high > 0:
                    position["peak_price"] = max(position["peak_price"], high)
                    position["max_floating_gain_pct"] = max(
                        position["max_floating_gain_pct"], high / unit_cost - 1.0,
                    )
                if low > 0:
                    position["max_floating_loss_pct"] = min(
                        position["max_floating_loss_pct"], low / unit_cost - 1.0,
                    )

            for symbol, position in list(positions.items()):
                if position["entry_date"] >= timestamp.date() or symbol in pending_sells:
                    continue
                context = daily_context.get((symbol, timestamp.date()))
                close = float(bars.get(symbol, {}).get("close", 0) or 0)
                ma_exit_period = self.config.strategy_params.ma_exit_period
                if ma_exit_period == 5:
                    previous_closes = list(context.get("previous_closes") or []) if context else []
                    ma_value = (
                        (sum(previous_closes[-4:]) + close) / 5
                        if len(previous_closes) >= 4 and close > 0
                        else 0.0
                    )
                else:
                    ma_key = f"previous_ma{ma_exit_period}"
                    ma_value = float(context.get(ma_key) or 0) if context else 0.0
                entry_price = float(position["entry_price"])
                peak_price = float(position["peak_price"])
                held_trade_days = (
                    trade_day_index[timestamp.date()]
                    - trade_day_index[position["entry_date"]]
                )
                exit_reason: str | None = None
                if (
                    close > 0
                    and self.config.strategy_params.stop_loss_pct > 0
                    and close <= position["entry_price"] * (1 - self.config.strategy_params.stop_loss_pct)
                ):
                    exit_reason = "stop_loss"
                elif (
                    close > 0
                    and self.config.take_profit_pct is not None
                    and close >= entry_price * (1 + self.config.take_profit_pct)
                ):
                    exit_reason = "take_profit"
                elif (
                    close > 0
                    and self.config.trailing_stop_pct is not None
                    and close <= peak_price * (1 - self.config.trailing_stop_pct)
                ):
                    exit_reason = "trailing_stop"
                elif (
                    close > 0
                    and self.config.trailing_take_profit_activate_pct is not None
                    and self.config.trailing_take_profit_drawdown_pct is not None
                    and peak_price >= entry_price * (1 + self.config.trailing_take_profit_activate_pct)
                    and close <= peak_price * (1 - self.config.trailing_take_profit_drawdown_pct)
                ):
                    exit_reason = "trailing_take_profit"
                elif (
                    self.config.max_hold_days is not None
                    and held_trade_days >= self.config.max_hold_days
                ):
                    exit_reason = "max_hold_days"
                elif close > 0 and ma_value > 0 and close < ma_value:
                    exit_reason = f"ma{ma_exit_period}_breakdown"
                if exit_reason is not None:
                    position["exit_reason"] = exit_reason
                    if self.config.exit_fill == "signal_minute_close":
                        bar = bars.get(symbol)
                        if not close_position(
                            symbol,
                            position,
                            bar,
                            "signal_minute_close",
                            timestamp,
                        ):
                            pending_sells[symbol] = "signal_minute_close"
                    else:
                        pending_sells[symbol] = "next_minute_open"

            candidates: list[Candidate] = []
            for symbol, bar in bars.items():
                if (
                    self.config.force_close_at_end
                    and self.config.end is not None
                    and timestamp.date() == trade_dates[-1]
                ):
                    continue
                current_date = timestamp.date()
                key = (symbol, current_date)
                context = daily_context.get(key)
                if context is None or symbol in positions or key in entered_today:
                    continue
                current_time = timestamp.time()
                if not is_in_scan_window(current_time, self.config.strategy_params):
                    continue
                if not _passes_basic_filter(
                    symbol, bar, self.config.basic_filter, self.config.symbol_names,
                ):
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
                        float(bar["high"]) > float(context["previous_high"])
                    ),
                    params=self.config.strategy_params,
                )
                if reason is not None:
                    candidates.append({
                        "symbol": symbol,
                        "volume_ratio": volume_ratio,
                        "today_return": today_return,
                        "previous_return": float(context["previous_change_pct"]),
                        "cumulative_amount": float(bar.get("cumulative_amount", bar.get("amount") or 0)),
                        "reason": reason,
                    })

            slots = self.config.max_positions - len(positions) - len(pending_buys)
            ranked_candidates = (
                rank_candidates(
                    candidates,
                    mode=self.config.candidate_sort,
                    weights=self.config.scoring,
                    score_min=self.config.score_min,
                    score_max=self.config.score_max,
                    watchlist_order=self.config.symbols,
                )
                if len(candidates) > max(slots, 0)
                else rank_candidates(candidates)
            )
            for candidate in ranked_candidates[:max(slots, 0)]:
                if self.config.entry_fill == "signal_minute_close":
                    bar = bars.get(candidate["symbol"])
                    if open_position(
                        candidate,
                        bar,
                        "signal_minute_close",
                        timestamp,
                        bars,
                    ):
                        entered_today.add((candidate["symbol"], timestamp.date()))
                else:
                    pending_buys.append({
                        **candidate,
                        "execution_timestamp": timestamp + timedelta(minutes=1),
                    })

            for symbol, bar in bars.items():
                close = float(bar.get("close") or 0)
                if close > 0:
                    latest_closes[symbol] = close

        final_date = trade_dates[-1]
        terminal_timestamp = max(grouped)
        if self.config.force_close_at_end:
            for symbol, position in list(positions.items()):
                latest = latest_bars.get(symbol)
                mark_timestamp, latest_bar = latest if latest else (
                    position["entry_datetime"], None,
                )
                final_bar = (
                    latest_bar
                    if mark_timestamp.date() == final_date
                    else None
                )
                if (
                    last_sell_attempt.get(symbol) == terminal_timestamp
                    and position.get("exit_block_reason")
                ):
                    continue
                context = daily_context.get((symbol, final_date)) or {}
                block_reason = _sell_block_reason(
                    symbol=symbol,
                    bar=final_bar,
                    previous_close=float(context.get("previous_close") or 0),
                    symbol_name=self.config.symbol_names.get(symbol, ""),
                    fill="signal_minute_close",
                )
                if block_reason is not None:
                    execution[block_reason] += 1
                    position["exit_block_reason"] = block_reason
                    continue
                position["exit_reason"] = "end_of_backtest"
                close_position(
                    symbol,
                    position,
                    final_bar,
                    "signal_minute_close",
                    mark_timestamp,
                )

        open_positions: list[dict] = []
        for symbol, position in positions.items():
            latest = latest_bars.get(symbol)
            mark_timestamp, mark_bar = latest if latest else (
                position["entry_datetime"], {},
            )
            mark_price = float(mark_bar.get("close") or position["entry_price"])
            market_value = position["shares"] * mark_price
            open_positions.append({
                "symbol": symbol,
                "entry_datetime": position["entry_datetime"].isoformat(sep=" "),
                "entry_date": str(position["entry_date"]),
                "entry_price": round(float(position["entry_price"]), 4),
                "shares": int(position["shares"]),
                "mark_datetime": mark_timestamp.isoformat(sep=" "),
                "mark_date": str(mark_timestamp.date()),
                "mark_price": round(mark_price, 4),
                "market_value": round(market_value, 2),
                "unrealized_pnl_amount": round(
                    market_value - position["entry_cost"], 2,
                ),
                "unrealized_pnl_pct": round(
                    market_value / position["entry_cost"] - 1.0,
                    6,
                ) if position["entry_cost"] else 0.0,
                "exit_block_reason": position.get("exit_block_reason"),
            })
        final_equity = cash + sum(
            float(position["market_value"]) for position in open_positions
        )
        if current_date is not None:
            snapshot(current_date)
        return {
            "cash": cash,
            "final_equity": final_equity,
            "trades": trades,
            "open_positions": open_positions,
            "execution": execution,
            "equity_curve": equity_curve, "drawdown_curve": drawdown_curve,
        }


class OpeningVolumeScanService:
    """Run the native opening-volume strategy against the TickFlow watchlist."""

    def __init__(self, repo) -> None:
        self.repo = repo

    def run(self, config: OpeningVolumeScanConfig) -> dict:
        started = perf_counter()
        symbols = [str(row["symbol"]) for row in watchlist.list_symbols() if row.get("symbol")]
        if not symbols:
            return {
                "as_of": str(config.as_of),
                "strategy": "opening_volume_portfolio",
                "rows": [],
                "total": 0,
                "elapsed_ms": 0.0,
            }
        raw_rows, contexts = _load_rows_and_context(
            self.repo,
            symbols,
            config.as_of,
            config.as_of,
            config.strategy_params.ma_exit_period,
        )
        grouped: dict[datetime, list[dict]] = {}
        for row in raw_rows:
            grouped.setdefault(row["datetime"], []).append(row)

        matched: set[str] = set()
        candidates: list[dict] = []
        for timestamp in sorted(grouped):
            if timestamp.date() != config.as_of:
                continue
            for bar in grouped[timestamp]:
                symbol = bar["symbol"]
                key = (symbol, timestamp.date())
                context = contexts.get(key)
                if context is None or symbol in matched:
                    continue
                if not is_in_scan_window(timestamp.time(), config.strategy_params):
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
                        float(bar["high"]) > float(context["previous_high"])
                    ),
                    params=config.strategy_params,
                )
                if reason is None:
                    continue
                matched.add(symbol)
                candidates.append({
                    "symbol": symbol,
                    "date": str(timestamp.date()),
                    "time": (timestamp + timedelta(minutes=1)).strftime("%H:%M"),
                    "close": float(bar["close"]),
                    "volume_ratio": volume_ratio,
                    "today_return": today_return,
                    "entry_reason": reason,
                })

        rows = rank_candidates(candidates)
        return {
            "as_of": str(config.as_of),
            "strategy": "opening_volume_portfolio",
            "rows": rows,
            "total": len(rows),
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        }


class MinutePortfolioService:
    """Load TickFlow K lines and adapt the native engine to the backtest result contract."""

    def __init__(self, repo) -> None:
        self.repo = repo

    @staticmethod
    def _monte_carlo(pnls: np.ndarray) -> dict[str, float | None]:
        pnls = pnls[np.isfinite(pnls)]
        if pnls.size < 3:
            return {"mc_maxdd_p50": None, "mc_maxdd_p95": None}
        rng = np.random.default_rng(42)
        samples = rng.choice(np.clip(pnls, -0.9999, None), size=(1000, pnls.size), replace=True)
        equity = np.cumprod(1.0 + samples, axis=1)
        peaks = np.maximum.accumulate(equity, axis=1)
        max_drawdowns = ((equity - peaks) / peaks).min(axis=1)
        return {
            "mc_maxdd_p50": round(float(np.percentile(max_drawdowns, 50)), 4),
            "mc_maxdd_p95": round(float(np.percentile(max_drawdowns, 5)), 4),
        }

    @classmethod
    def _stats(cls, executed: dict, config: MinutePortfolioConfig) -> dict:
        final_equity = float(executed.get("final_equity", executed["cash"]))
        total_return = final_equity / config.initial_capital - 1.0
        days = max((config.end - config.start).days, 1)
        annual_return = (
            (1.0 + total_return) ** (365.25 / days) - 1.0
            if total_return > -1.0 else total_return
        )
        values = np.array(
            [config.initial_capital]
            + [float(row["value"]) for row in executed.get("equity_curve", [])],
            dtype=float,
        )
        daily_returns = values[1:] / values[:-1] - 1.0 if values.size > 1 else np.array([])
        volatility = float(np.std(daily_returns)) if daily_returns.size > 1 else 0.0
        sharpe = (
            float(np.mean(daily_returns) / volatility * np.sqrt(252))
            if volatility > 0 else 0.0
        )
        downside = np.minimum(daily_returns, 0.0)
        downside_deviation = (
            float(np.sqrt(np.mean(downside ** 2))) if daily_returns.size > 1 else 0.0
        )
        sortino = (
            float(np.mean(daily_returns) / downside_deviation * np.sqrt(252))
            if downside_deviation > 0 else None
        )
        pnls = np.array([float(trade.get("pnl_pct", 0.0)) for trade in executed["trades"]])
        n_trades = int(pnls.size)
        drawdowns = [float(row["value"]) for row in executed.get("drawdown_curve", [])]
        stats = {
            "total_return": round(total_return, 6),
            "annual_return": round(float(annual_return), 6),
            "sharpe": round(sharpe, 4),
            "sortino": round(sortino, 4) if sortino is not None else None,
            "max_drawdown": round(min(drawdowns, default=0.0), 6),
            "win_rate": round(float(np.mean(pnls > 0)), 6) if n_trades else 0.0,
            "n_trades": n_trades,
            "final_equity": round(final_equity, 2),
            "total_trade_count": n_trades,
            "end_balance": final_equity,
        }
        stats.update(cls._monte_carlo(pnls))
        return stats

    @staticmethod
    def _per_symbol(trades: list[dict]) -> list[dict]:
        grouped: dict[str, list[dict]] = {}
        for trade in trades:
            grouped.setdefault(str(trade["symbol"]), []).append(trade)
        rows = []
        for symbol, symbol_trades in grouped.items():
            pnls = [float(trade.get("pnl_pct", 0.0)) for trade in symbol_trades]
            row = {
                "symbol": symbol,
                "n_trades": len(pnls),
                "total_return": round(float(np.prod(1.0 + np.array(pnls)) - 1.0), 6),
                "win_rate": round(sum(pnl > 0 for pnl in pnls) / len(pnls), 6),
                "best": round(max(
                    float(trade.get("max_floating_gain_pct", 0.0))
                    for trade in symbol_trades
                ), 6),
                "worst": round(min(
                    float(trade.get("max_floating_loss_pct", 0.0))
                    for trade in symbol_trades
                ), 6),
            }
            name = next((trade.get("name") for trade in symbol_trades if trade.get("name")), None)
            if name:
                row["name"] = str(name)
            rows.append(row)
        return sorted(rows, key=lambda row: row["total_return"], reverse=True)

    def _benchmark(self, start: date, end: date) -> list[dict]:
        try:
            frame = self.repo.get_index_daily(
                "000001.XSHG", start, end, columns=["date", "close"],
            )
        except Exception:
            return []
        if frame.is_empty() or "close" not in frame.columns:
            return []
        return [
            {"date": str(row["date"])[:10], "close": round(float(row["close"]), 4)}
            for row in frame.sort("date").iter_rows(named=True)
            if row["close"] is not None and float(row["close"]) > 0
        ]

    def run(
        self,
        config: MinutePortfolioConfig,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        if config.start is None or config.end is None:
            raise ValueError("start and end are required")

        def emit(day: int, label: str, equity: float) -> None:
            if progress_callback:
                progress_callback({
                    "day": max(0, min(day, 1000)), "total": 1000,
                    "date": label, "equity": round(float(equity), 2),
                })

        emit(0, "准备回测", config.initial_capital)
        if config.minute_data_dir:
            total_reads = max(len(config.symbols) * 2, 1)

            def on_read(done: int, total: int) -> None:
                emit(round(done / total * 650), f"读取分钟数据 {done}/{total}", config.initial_capital)

            repo = LocalMinuteParquetRepository(
                config.minute_data_dir,
                progress_callback=on_read,
                progress_total=total_reads,
            )
        else:
            repo = self.repo
        raw_rows, contexts = _load_rows_and_context(
            repo,
            config.symbols,
            config.start,
            config.end,
            config.strategy_params.ma_exit_period,
        )
        raw_rows = [
            row for row in raw_rows
            if config.start <= row["datetime"].date() <= config.end
            and row["datetime"].time() >= time(9, 30)
        ]
        emit(650, f"分钟数据就绪 {len(config.symbols)} 只股票", config.initial_capital)

        def on_trade(done: int, total: int, equity: float, trading_day: date) -> None:
            progress = 650 + round(done / max(total, 1) * 300)
            emit(progress, f"撮合交易日 {done}/{total} ({trading_day})", equity)

        name_map: dict[str, str] = {}
        get_name_map = getattr(self.repo, "get_name_map", None)
        if callable(get_name_map):
            try:
                name_symbols = [
                    LocalMinuteParquetRepository._symbol(symbol)
                    for symbol in config.symbols
                ]
                name_map = get_name_map(name_symbols) or {}
            except Exception:
                name_map = {}
        engine_config = replace(config, symbol_names={
            LocalMinuteParquetRepository._symbol(str(symbol)): str(name)
            for symbol, name in name_map.items()
        })
        executed = MinutePortfolioEngine(engine_config).run(
            raw_rows, contexts, progress_callback=on_trade,
        )
        for trade in executed["trades"]:
            name = name_map.get(str(trade["symbol"]))
            if name:
                trade["name"] = str(name)
        for position in executed.get("open_positions", []):
            name = name_map.get(str(position["symbol"]))
            if name:
                position["name"] = str(name)
        final_equity = float(executed.get("final_equity", executed["cash"]))
        emit(950, "计算统计与基准", final_equity)
        equity_curve = executed.get("equity_curve") or [{
            "date": str(config.end), "value": round(final_equity, 2),
            "cash": round(float(executed["cash"]), 2),
            "positions": len(executed.get("open_positions", [])),
        }]
        drawdown_curve = executed.get("drawdown_curve") or [{"date": str(config.end), "value": 0.0}]
        executed["equity_curve"] = equity_curve
        executed["drawdown_curve"] = drawdown_curve
        result = {
            "run_id": uuid4().hex,
            "config": {"engine": "minute_portfolio", "frequency": "1m", "symbols": config.symbols,
                       "initial_capital": config.initial_capital, "max_positions": config.max_positions,
                       "cash_reserve_ratio": config.cash_reserve_ratio,
                       "max_buy_volume_ratio": config.max_buy_volume_ratio,
                       "candidate_sort": config.candidate_sort,
                       "entry_fill": config.entry_fill,
                       "exit_fill": config.exit_fill,
                       "force_close_at_end": config.force_close_at_end},
            "stats": self._stats(executed, config),
            "equity_curve": equity_curve,
            "drawdown_curve": drawdown_curve,
            "benchmark_curve": self._benchmark(config.start, config.end),
            "trades": executed["trades"],
            "open_positions": executed.get("open_positions", []),
            "execution": executed.get("execution", {}),
            "per_symbol_stats": self._per_symbol(executed["trades"]),
            "strategy_info": {"id": "opening_volume_portfolio", "name": "早盘放量组合", "source": "native"},
        }
        emit(1000, "完成", final_equity)
        return result
