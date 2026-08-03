"""Build local stock adjustment factors from standard daily bars."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import polars as pl


class LocalAdjustmentDataError(ValueError):
    """Raised when local daily data cannot reproduce adjustment factors."""


@dataclass
class LocalAdjFactorBuildSummary:
    status: str
    dry_run: bool
    rows_read: int = 0
    rows_written: int = 0
    event_rows: int = 0
    earliest_date: str | None = None
    latest_date: str | None = None
    built_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LocalAdjFactorBuilder:
    """Derive event factors from raw ``kline_daily`` ``pre_close`` values.

    ``ex_factor`` follows TickFlow's existing contract: on an ex-rights day it
    is ``previous_raw_close / current_pre_close`` and it is one on ordinary
    trading days.  One row is retained for every daily bar so qfq consumers can
    reject incomplete coverage instead of silently mixing price bases.
    """

    SOURCE = "local_pre_close"
    VERSION = 1

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "user_data" / "local_adj_factor.json"

    def run(self, *, dry_run: bool = False) -> LocalAdjFactorBuildSummary:
        source = self.data_dir / "kline_daily"
        files = list(source.rglob("*.parquet")) if source.exists() else []
        if not files:
            raise LocalAdjustmentDataError("缺少标准 kline_daily 数据，无法构建本地复权因子")
        scan = pl.scan_parquet(str(source / "**" / "*.parquet"))
        required = {"symbol", "date", "close", "pre_close"}
        columns = set(scan.collect_schema().names())
        missing = required - columns
        if missing:
            raise LocalAdjustmentDataError(
                f"标准 kline_daily 缺少字段: {', '.join(sorted(missing))}；请导入保留 pre_close 的原始日线"
            )
        raw = (
            scan.select(
                pl.col("symbol").cast(pl.Utf8),
                pl.col("date").cast(pl.Date),
                pl.col("close").cast(pl.Float64, strict=False),
                pl.col("pre_close").cast(pl.Float64, strict=False),
            )
            .drop_nulls(["symbol", "date", "close"])
            .sort(["symbol", "date"])
            .collect(engine="streaming")
        )
        if raw.group_by(["symbol", "date"]).len().filter(pl.col("len") > 1).height:
            raise LocalAdjustmentDataError("标准 kline_daily 存在重复的股票日期记录，拒绝构建复权因子")
        previous = pl.col("close").shift(1).over("symbol")
        invalid = raw.with_columns(previous.alias("_previous_close")).filter(
            pl.col("_previous_close").is_not_null()
            & ((pl.col("_previous_close") <= 0) | (pl.col("pre_close").is_null()) | (pl.col("pre_close") <= 0))
        )
        if invalid.height:
            sample = ", ".join(
                f"{row['symbol']} {row['date']}" for row in invalid.head(5).iter_rows(named=True)
            )
            raise LocalAdjustmentDataError(f"标准日线存在无效 pre_close，无法生成严格复权因子: {sample}")
        factors = self._derive(raw)
        summary = LocalAdjFactorBuildSummary(
            status="preview" if dry_run else "succeeded",
            dry_run=dry_run,
            rows_read=raw.height,
            rows_written=factors.height,
            event_rows=factors.filter(pl.col("is_event")).height,
            earliest_date=factors["trade_date"].min().isoformat() if factors.height else None,
            latest_date=factors["trade_date"].max().isoformat() if factors.height else None,
        )
        if dry_run:
            return summary
        self._publish(factors)
        summary.built_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    @classmethod
    def _derive(cls, raw: pl.DataFrame) -> pl.DataFrame:
        previous = pl.col("close").shift(1).over("symbol")
        has_previous = previous.is_not_null()
        valid = (~has_previous) | ((previous > 0) & (pl.col("pre_close") > 0))
        ex_factor = pl.when(has_previous & valid).then(previous / pl.col("pre_close")).otherwise(1.0)
        return (
            raw.with_columns(
                ex_factor.alias("ex_factor"),
                (has_previous & valid & ((ex_factor - 1.0).abs() > 1e-9)).alias("is_event"),
            )
            .select(
                "symbol", pl.col("date").alias("trade_date"), "ex_factor", "is_event",
                pl.lit(cls.SOURCE).alias("source"), pl.lit(cls.VERSION).alias("factor_version"),
            )
            .sort(["symbol", "trade_date"])
        )

    def _publish(self, factors: pl.DataFrame) -> None:
        output = self.data_dir / "adj_factor" / "all.parquet"
        output.parent.mkdir(parents=True, exist_ok=True)
        existing = pl.read_parquet(output) if output.exists() else pl.DataFrame()
        if not existing.is_empty():
            existing = existing.join(factors.select("symbol", "trade_date"), on=["symbol", "trade_date"], how="anti")
            merged = pl.concat([existing, factors], how="diagonal_relaxed")
        else:
            merged = factors
        temporary = output.with_name(f"{output.name}.tmp")
        merged.sort(["symbol", "trade_date"]).write_parquet(temporary)
        temporary.replace(output)
