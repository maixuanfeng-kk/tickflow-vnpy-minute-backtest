"""Contracts shared by registered stock-pool strategies."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import polars as pl


@dataclass(frozen=True)
class StockPoolInput:
    month: str
    as_of_date: date
    daily: pl.DataFrame
    financials: pl.DataFrame
    instruments: pl.DataFrame = field(default_factory=pl.DataFrame)
    shares: pl.DataFrame = field(default_factory=pl.DataFrame)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class StockPoolStrategySpec:
    id: str
    name: str
    description: str
    strategy_class: type
    parameters: tuple[dict[str, object], ...] = ()
    version: str = "1"

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "parameters": list(self.parameters),
            "version": self.version,
        }


class StockPoolStrategy(Protocol):
    def __init__(self, params: dict[str, object]) -> None: ...

    def build(self, data: StockPoolInput) -> pl.DataFrame: ...
