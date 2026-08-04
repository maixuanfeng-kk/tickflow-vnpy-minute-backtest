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

    def resolve_params(self, params: dict[str, object] | None) -> dict[str, object]:
        """Validate public strategy options and fill their declared defaults."""
        supplied = params or {}
        definitions = {str(item["id"]): item for item in self.parameters}
        unknown = sorted(set(supplied) - set(definitions))
        if unknown:
            raise ValueError(f"策略 {self.id} 不支持参数：{', '.join(unknown)}")

        resolved: dict[str, object] = {}
        for parameter_id, definition in definitions.items():
            value = supplied.get(parameter_id, definition.get("default"))
            allowed_values = {option.get("value") for option in definition.get("options", ())}
            if allowed_values and value not in allowed_values:
                allowed = ", ".join(str(item) for item in sorted(allowed_values))
                raise ValueError(f"参数 {parameter_id} 取值无效，应为：{allowed}")
            resolved[parameter_id] = value
        return resolved

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
