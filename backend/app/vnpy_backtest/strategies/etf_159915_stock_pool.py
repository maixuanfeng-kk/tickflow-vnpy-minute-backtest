"""ETF159915-timed stock-pool selection logic."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import DailyReference, OrderIntent, PortfolioContext


@dataclass(frozen=True)
class StockCandidate:
    symbol: str
    previous_return: float
    stock_return: float
    volume_ratio: float


def select_stock_candidates(
    bars: Mapping[str, BarData],
    references: Mapping[str, DailyReference],
    *,
    etf_return: float,
    now: datetime,
    cumulative_volumes: Mapping[str, float],
    pool_symbols: set[str],
) -> list[StockCandidate]:
    """Return eligible stocks ordered by current-day volume expansion."""
    candidates: list[StockCandidate] = []
    current_time = now.time()
    for symbol in sorted(pool_symbols):
        bar = bars.get(symbol)
        reference = references.get(symbol)
        if bar is None or reference is None:
            continue
        previous_close = reference.previous_close
        closes = reference.closes
        if previous_close is None or previous_close <= 0 or len(closes) < 2 or closes[-2] <= 0:
            continue
        previous_return = previous_close / closes[-2] - 1.0
        stock_return = float(bar.close_price) / previous_close - 1.0
        previous_volume = reference.previous_cumulative_volumes.get(current_time, 0.0)
        current_volume = cumulative_volumes.get(symbol, 0.0)
        if previous_volume <= 0:
            continue
        volume_ratio = current_volume / previous_volume
        if not (-0.06 <= previous_return <= 0.04):
            continue
        if not (stock_return > etf_return and stock_return <= etf_return + 0.02):
            continue
        if float(bar.close_price) <= float(bar.open_price):
            continue
        if volume_ratio < 1.3:
            continue
        candidates.append(StockCandidate(symbol, previous_return, stock_return, volume_ratio))
    return sorted(candidates, key=lambda item: (-item.volume_ratio, item.symbol))


class Etf159915StockPoolStrategy:
    """Trade monthly stock pools only when the ETF shadow pass signals."""

    def __init__(self, params: Mapping[str, object]) -> None:
        self.max_positions = max(1, int(params.get("max_positions", 10)))
        self.etf_events = self._normalize_events(params.get("etf_events", {}))
        self.monthly_pools = self._normalize_pools(params.get("monthly_pools", {}))
        self._day: date | None = None
        self._cumulative_volumes: dict[str, float] = {}

    def on_minute(self, bars: Mapping[str, BarData], context: PortfolioContext) -> list[OrderIntent]:
        trading_day = context.timestamp.date()
        if self._day != trading_day:
            self._day = trading_day
            self._cumulative_volumes = {}
        for symbol, bar in bars.items():
            self._cumulative_volumes[symbol] = self._cumulative_volumes.get(symbol, 0.0) + float(bar.volume)

        event = self.etf_events.get(context.timestamp)
        held = set(context.positions)
        if event and event["direction"] == "sell":
            return [OrderIntent(symbol, Direction.SHORT, "etf_sell") for symbol in sorted(held)]

        intents: list[OrderIntent] = []
        for symbol in sorted(held):
            bar = bars.get(symbol)
            reference = context.daily_references.get(symbol)
            if bar is not None and reference is not None and reference.previous_low is not None and float(bar.close_price) < float(reference.previous_low):
                intents.append(OrderIntent(symbol, Direction.SHORT, "below_previous_low"))

        if not event or event["direction"] != "buy":
            return intents
        pool_symbols = self._pool_for_date(trading_day)
        available_slots = max(self.max_positions - len(held), 0)
        if available_slots <= 0:
            return intents
        candidates = select_stock_candidates(
            bars,
            context.daily_references,
            etf_return=float(event["etf_return"]),
            now=context.timestamp,
            cumulative_volumes=self._cumulative_volumes,
            pool_symbols=pool_symbols - held,
        )[:available_slots]
        for rank, candidate in enumerate(candidates, start=1):
            intents.append(OrderIntent(
                candidate.symbol,
                Direction.LONG,
                f"etf_buy:{event['reason']}",
                diagnostic={
                    "matched_conditions": ["etf_buy_stock_pool"],
                    "candidate_rank": rank,
                    "previous_return": candidate.previous_return,
                    "stock_return": candidate.stock_return,
                    "etf_return": float(event["etf_return"]),
                    "volume_ratio": candidate.volume_ratio,
                },
            ))
        return intents

    @staticmethod
    def _normalize_events(value: object) -> dict[datetime, dict[str, object]]:
        if not isinstance(value, Mapping):
            return {}
        result: dict[datetime, dict[str, object]] = {}
        for raw_timestamp, raw_event in value.items():
            if not isinstance(raw_event, Mapping):
                continue
            try:
                timestamp = raw_timestamp if isinstance(raw_timestamp, datetime) else datetime.fromisoformat(str(raw_timestamp))
                direction = str(raw_event.get("direction", ""))
                if direction not in {"buy", "sell"}:
                    continue
                result[timestamp] = {
                    "direction": direction,
                    "etf_return": float(raw_event.get("etf_return", 0.0)),
                    "reason": str(raw_event.get("reason", direction)),
                }
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _normalize_pools(value: object) -> list[tuple[date, set[str]]]:
        if not isinstance(value, Mapping):
            return []
        result: list[tuple[date, set[str]]] = []
        for raw_month, raw_pool in value.items():
            if not isinstance(raw_pool, Mapping):
                continue
            try:
                effective = date.fromisoformat(str(raw_pool.get("effective_date", f"{raw_month}-01")))
            except ValueError:
                continue
            symbols = raw_pool.get("symbols", [])
            if isinstance(symbols, (list, tuple, set)):
                result.append((effective, {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}))
        return sorted(result)

    def _pool_for_date(self, trading_day: date) -> set[str]:
        selected: set[str] = set()
        for effective, symbols in self.monthly_pools:
            if effective <= trading_day:
                selected = symbols
            else:
                break
        return selected
