"""Local XBX adjustment-factor storage and point-in-time price projections.

The XBX daily dataset is the authoritative raw-price source.  Its ``pre_close``
is the exchange ex-rights reference close, so a daily event multiplier is
``previous_raw_close / pre_close``.  We persist daily cumulative factors to
make the no-look-ahead projection inexpensive for stock-pool and minute runs.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from app.services.local_adj_factor import LocalAdjustmentDataError


class AdjustmentDataError(LocalAdjustmentDataError):
    """Raised when a requested front-adjusted price cannot be reproduced."""


def factor_dataset_path(data_dir: Path, dataset: str = "adj_factor_xbx") -> Path:
    return Path(data_dir) / dataset


def load_local_factors(
    data_dir: Path,
    *,
    symbols: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    dataset: str = "adj_factor_xbx",
) -> pl.DataFrame:
    """Read locally derived cumulative factors without consulting a provider."""
    root = factor_dataset_path(data_dir, dataset)
    files = list(root.rglob("*.parquet")) if root.exists() else []
    if not files:
        raise AdjustmentDataError("缺少本地 XBX 复权因子，请先运行 build_local_adj_factor_xbx")
    lf = pl.scan_parquet(str(root / "**" / "*.parquet"))
    columns = set(lf.collect_schema().names())
    required = {"symbol", "trade_date", "cum_factor", "quality_status"}
    if not required.issubset(columns):
        # The older standard adj_factor store only has event factors.  It stays
        # readable as a compatibility fallback while the local Tushare store
        # remains the primary source for new portfolio runs.
        if dataset == "adj_factor" and {"symbol", "trade_date", "ex_factor"}.issubset(columns):
            lf = lf.select(
                pl.col("symbol").cast(pl.Utf8),
                pl.col("trade_date").cast(pl.Date),
                pl.col("ex_factor").cast(pl.Float64, strict=False).fill_null(1.0),
                pl.col("is_event").cast(pl.Boolean, strict=False).fill_null(False)
                if "is_event" in columns else (pl.col("ex_factor") != 1.0),
            ).sort(["symbol", "trade_date"]).with_columns(
                pl.col("ex_factor").cum_prod().over("symbol").alias("cum_factor"),
                pl.lit("ok").alias("quality_status"),
            )
        else:
            raise AdjustmentDataError("本地 XBX 复权因子格式不完整，请重新构建")
    if symbols:
        lf = lf.filter(pl.col("symbol").is_in(symbols))
    if start is not None:
        lf = lf.filter(pl.col("trade_date").cast(pl.Date) >= start)
    if end is not None:
        lf = lf.filter(pl.col("trade_date").cast(pl.Date) <= end)
    return (
        lf.select(
            pl.col("symbol").cast(pl.Utf8),
            pl.col("trade_date").cast(pl.Date),
            pl.col("cum_factor").cast(pl.Float64),
            pl.col("ex_factor").cast(pl.Float64, strict=False),
            pl.col("is_event").cast(pl.Boolean, strict=False).fill_null(False),
            pl.col("quality_status").cast(pl.Utf8),
        )
        .sort(["symbol", "trade_date"])
        .collect(engine="streaming")
    )


def project_to_reference(
    frame: pl.DataFrame,
    factors: pl.DataFrame,
    reference_date: date,
    *,
    date_column: str = "date",
    price_columns: tuple[str, ...] = ("open", "high", "low", "close"),
    prefix: str = "signal_",
) -> pl.DataFrame:
    """Project raw prices to ``reference_date`` using only factors through it.

    For a raw price at date d, the returned price is raw * F_d / F_reference.
    Hence bars on the reference date stay raw, while prior bars become qfq.
    """
    if frame.is_empty():
        return frame
    if factors.is_empty():
        raise AdjustmentDataError("本地 XBX 复权因子为空")
    needed_symbols = frame["symbol"].unique().to_list()
    valid = factors.filter(
        (pl.col("trade_date") <= reference_date)
        & pl.col("symbol").is_in(needed_symbols)
        & (pl.col("quality_status") == "ok")
    )
    reference = valid.filter(pl.col("trade_date") == reference_date).select(
        "symbol", pl.col("cum_factor").alias("_reference_factor")
    )
    missing_reference = set(needed_symbols) - set(reference["symbol"].to_list())
    if missing_reference:
        sample = ", ".join(sorted(missing_reference)[:5])
        raise AdjustmentDataError(f"复权因子未覆盖参考日 {reference_date}: {sample}")
    prices = frame.join(
        valid.select("symbol", "trade_date", "cum_factor"),
        left_on=["symbol", date_column], right_on=["symbol", "trade_date"], how="left",
    ).join(reference, on="symbol", how="left")
    if prices.filter(pl.col("cum_factor").is_null()).height:
        raise AdjustmentDataError("复权因子未覆盖所需历史价格")
    columns = [column for column in price_columns if column in prices.columns]
    return prices.with_columns(
        *[
            (pl.col(column).cast(pl.Float64) * pl.col("cum_factor") / pl.col("_reference_factor"))
            .alias(f"{prefix}{column}")
            for column in columns
        ]
    ).drop(["cum_factor", "_reference_factor"])


def factor_map(factors: pl.DataFrame) -> dict[tuple[str, date], float]:
    """Make the small date-range factor lookup used by minute backtests."""
    invalid = factors.filter(pl.col("quality_status") != "ok")
    if invalid.height:
        raise AdjustmentDataError("所选分钟回测区间存在质量不合格的本地复权因子")
    return {
        (str(row["symbol"]), row["trade_date"]): float(row["cum_factor"])
        for row in factors.to_dicts()
    }
