"""Point-in-time qfq projections for vn.py minute strategy signals.

Minute bars are intentionally never transformed or persisted.  This object only
supplies the daily factors required to express completed-day references on the
current signal day's scale.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from app.pricing.adjustment import AdjustmentDataError, factor_map, load_local_factors


@dataclass(frozen=True)
class MinuteSignalPriceProjector:
    basis: str
    factors: dict[tuple[str, date], float]
    event_dates: dict[str, set[date]]

    @classmethod
    def load(
        cls,
        data_dir: Path,
        symbols: list[str],
        start: date,
        end: date,
        basis: str,
        *,
        factor_dataset: str = "adj_factor_xbx",
    ) -> "MinuteSignalPriceProjector":
        if basis not in {"qfq", "raw"}:
            raise ValueError("signal_price_basis 必须是 qfq 或 raw")
        if basis == "raw":
            return cls(basis, {}, {})
        factors = load_local_factors(
            data_dir, symbols=symbols, start=start, end=end, dataset=factor_dataset,
        )
        lookup = factor_map(factors)
        events: dict[str, set[date]] = defaultdict(set)
        for row in factors.filter(pl.col("is_event")).select("symbol", "trade_date").iter_rows(named=True):
            events[str(row["symbol"])].add(row["trade_date"])
        return cls(basis, lookup, dict(events))

    def factor(self, symbol: str, trading_day: date) -> float:
        if self.basis == "raw":
            return 1.0
        value = self.factors.get((symbol, trading_day))
        if value is None:
            raise AdjustmentDataError(f"复权因子未覆盖 {symbol} {trading_day}")
        return value

    def scale(self, symbol: str, price_day: date, reference_day: date) -> float:
        if self.basis == "raw":
            return 1.0
        return self.factor(symbol, price_day) / self.factor(symbol, reference_day)

    def has_event_while_held(self, symbol: str, entry_day: date, exit_day: date) -> bool:
        return any(entry_day < event_day <= exit_day for event_day in self.event_dates.get(symbol, set()))
