"""Strategy-neutral contracts for local vn.py minute backtests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum
from typing import Mapping, Protocol, Sequence

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData


class StrategyKind(StrEnum):
    PORTFOLIO = "portfolio"


@dataclass(frozen=True)
class StrategyParameter:
    name: str
    label: str
    default: object
    kind: str = "number"
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class StrategySpec:
    id: str
    name: str
    kind: StrategyKind
    strategy_class: type
    parameters: tuple[StrategyParameter, ...] = ()
    min_symbols: int = 1
    max_symbols: int = 1000
    description: str = ""

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "min_symbols": self.min_symbols,
            "max_symbols": self.max_symbols,
            "description": self.description,
            "parameters": [parameter.__dict__ for parameter in self.parameters],
        }


@dataclass(frozen=True)
class DailyReference:
    previous_open: float | None = None
    previous_close: float | None = None
    # Execution rules, such as price limits, must use the unadjusted close.
    raw_previous_close: float | None = None
    previous_high: float | None = None
    previous_low: float | None = None
    previous_volume: float | None = None
    closes: tuple[float, ...] = ()
    # Cumulative volume at each minute of the previous completed trading day.
    previous_cumulative_volumes: Mapping[time, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PortfolioPositionView:
    symbol: str
    volume: int
    average_cost: float
    entry_date: date | None
    high_water_price: float | None = None
    entry_trading_day_index: int | None = None


@dataclass(frozen=True)
class PortfolioContext:
    timestamp: datetime
    cash: float
    reserved_cash: float
    positions: Mapping[str, PortfolioPositionView]
    daily_references: Mapping[str, DailyReference]
    instrument_names: Mapping[str, str] = field(default_factory=dict)
    trading_day_index: int = 0

    @property
    def available_cash(self) -> float:
        return max(self.cash - self.reserved_cash, 0.0)


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    direction: Direction
    reason: str
    volume: int | None = None
    target_cash: float | None = None
    # Strategy-owned, JSON-safe context for the result-page signal diagnostic.
    # It never participates in sizing or matching.
    diagnostic: Mapping[str, object] = field(default_factory=dict)


class PortfolioMinuteStrategy(Protocol):
    """A future stock-pool strategy implements signals only, never execution.

    The registry constructs a strategy with its validated parameter dictionary.
    Strategy code must only inspect completed/current bars and return intents; the
    portfolio engine owns cash, order matching, trading costs and position state.
    """

    def __init__(self, params: Mapping[str, object]) -> None: ...

    def on_minute(
        self,
        bars: Mapping[str, BarData],
        context: PortfolioContext,
    ) -> Sequence[OrderIntent]: ...
