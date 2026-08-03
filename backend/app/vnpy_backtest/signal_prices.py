"""Point-in-time qfq projections for vn.py minute strategy signals."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from app.services.local_adj_factor import LocalAdjustmentDataError


@dataclass(frozen=True)
class MinuteSignalPriceProjector:
    basis: str
    factors: dict[tuple[str, date], float]
    event_dates: dict[str, set[date]]

    @classmethod
    def load(cls, data_dir: Path, symbols: list[str], end: date, basis: str) -> "MinuteSignalPriceProjector":
        if basis not in {"qfq", "raw"}:
            raise ValueError("signal_price_basis must be qfq or raw")
        if basis == "raw":
            return cls("raw", {}, {})
        root = Path(data_dir) / "adj_factor"
        if not root.exists() or not list(root.rglob("*.parquet")):
            raise LocalAdjustmentDataError("缺少复权因子；请先运行 python -m app.scripts.build_local_adj_factor")
        scan = pl.scan_parquet(str(root / "**" / "*.parquet"))
        columns = set(scan.collect_schema().names())
        required = {"symbol", "trade_date", "ex_factor"}
        if not required.issubset(columns):
            raise LocalAdjustmentDataError("复权因子格式不完整；请重新运行 python -m app.scripts.build_local_adj_factor")
        event_expr = (
            pl.col("is_event").cast(pl.Boolean, strict=False).fill_null(False)
            if "is_event" in columns
            else pl.lit(False).alias("is_event")
        )
        frame = (
            scan.select(
                pl.col("symbol").cast(pl.Utf8), pl.col("trade_date").cast(pl.Date),
                pl.col("ex_factor").cast(pl.Float64, strict=False), event_expr,
            )
            .filter((pl.col("symbol").is_in(symbols)) & (pl.col("trade_date") <= end))
            .sort(["symbol", "trade_date"])
            .collect(engine="streaming")
        )
        if frame.is_empty():
            raise LocalAdjustmentDataError("所选股票没有复权因子；请先运行 python -m app.scripts.build_local_adj_factor")
        frame = frame.unique(subset=["symbol", "trade_date"], keep="last").sort(["symbol", "trade_date"])
        cumulative = frame.with_columns(pl.col("ex_factor").cum_prod().over("symbol").alias("cum_factor"))
        lookup = {(str(row["symbol"]), row["trade_date"]): float(row["cum_factor"]) for row in cumulative.iter_rows(named=True)}
        events: dict[str, set[date]] = defaultdict(set)
        for row in cumulative.filter(pl.col("is_event")).iter_rows(named=True):
            events[str(row["symbol"])].add(row["trade_date"])
        return cls("qfq", lookup, dict(events))

    def factor(self, symbol: str, trading_day: date) -> float:
        if self.basis == "raw":
            return 1.0
        value = self.factors.get((symbol, trading_day))
        if value is None:
            raise LocalAdjustmentDataError(f"复权因子未覆盖 {symbol} {trading_day}")
        return value

    def scale(self, symbol: str, price_day: date, reference_day: date) -> float:
        return self.factor(symbol, price_day) / self.factor(symbol, reference_day)

    def has_event_while_held(self, symbol: str, entry_day: date, exit_day: date) -> bool:
        return any(entry_day < item <= exit_day for item in self.event_dates.get(symbol, set()))
