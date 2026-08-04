"""Build auditable local adjustment factors from the XBX daily dataset."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl

from app.pricing.adjustment import factor_dataset_path


@dataclass
class LocalAdjFactorBuildSummary:
    status: str
    dry_run: bool
    rows_read: int = 0
    rows_written: int = 0
    event_rows: int = 0
    invalid_rows: int = 0
    earliest_date: str | None = None
    latest_date: str | None = None
    built_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LocalXbxAdjFactorBuilder:
    """Build one daily cumulative-factor row per XBX daily bar.

    Keeping daily rows makes factor coverage explicit: qfq consumers can reject
    incomplete symbols rather than silently mixing raw and adjusted prices.
    """

    VERSION = 1

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "user_data" / "local_adj_factor_xbx.json"

    def run(self, *, dry_run: bool = False) -> LocalAdjFactorBuildSummary:
        source = self.data_dir / "kline_daily_xbx"
        files = list(source.rglob("*.parquet")) if source.exists() else []
        if not files:
            raise ValueError("缺少 kline_daily_xbx 数据，无法构建本地复权因子")
        scan = pl.scan_parquet(str(source / "**" / "*.parquet"))
        columns = set(scan.collect_schema().names())
        required = {"symbol", "date", "close", "pre_close"}
        if not required.issubset(columns):
            raise ValueError(f"kline_daily_xbx 缺少字段: {', '.join(sorted(required - columns))}")
        raw = (
            scan.select(
                pl.col("symbol").cast(pl.Utf8),
                pl.col("date").cast(pl.Date),
                pl.col("close").cast(pl.Float64, strict=False),
                pl.col("pre_close").cast(pl.Float64, strict=False),
            )
            .sort(["symbol", "date"])
            .collect(engine="streaming")
        )
        duplicate = raw.group_by(["symbol", "date"]).len().filter(pl.col("len") > 1)
        if duplicate.height:
            raise ValueError("kline_daily_xbx 存在重复的股票日期记录，拒绝构建复权因子")
        result = self._derive(raw)
        summary = LocalAdjFactorBuildSummary(
            status="preview" if dry_run else "succeeded",
            dry_run=dry_run,
            rows_read=raw.height,
            rows_written=result.height,
            event_rows=result.filter(pl.col("is_event")).height,
            invalid_rows=result.filter(pl.col("quality_status") != "ok").height,
            earliest_date=result["trade_date"].min().isoformat() if result.height else None,
            latest_date=result["trade_date"].max().isoformat() if result.height else None,
        )
        if dry_run:
            return summary
        stage = self.data_dir / ".adj-factor-xbx-staging" / uuid4().hex
        try:
            self._write_partitions(result, stage / "final")
            metadata = {
                "dataset": "adj_factor_xbx",
                "version": self.VERSION,
                "source_dataset": "kline_daily_xbx",
                "method": "ex_factor = previous_raw_close / current_pre_close",
                "price_basis": "raw XBX close and exchange ex-rights pre_close",
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            (stage / "final" / "_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            self._publish(stage / "final")
            summary.built_at = metadata["updated_at"]
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            return summary
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            try:
                stage.parent.rmdir()
            except OSError:
                pass

    @staticmethod
    def _derive(raw: pl.DataFrame) -> pl.DataFrame:
        previous = pl.col("close").shift(1).over("symbol")
        has_previous = previous.is_not_null()
        valid = (~has_previous) | (
            previous.is_not_null() & (previous > 0) & pl.col("pre_close").is_not_null() & (pl.col("pre_close") > 0)
        )
        ex_factor = pl.when(has_previous & valid).then(previous / pl.col("pre_close")).otherwise(1.0)
        # A discrepancy at normal decimal precision represents an exchange
        # ex-rights reference, not a market price move (today's close is never
        # used in this calculation).
        is_event = has_previous & valid & ((ex_factor - 1.0).abs() > 1e-9)
        base = raw.with_columns(
            previous.alias("previous_raw_close"),
            ex_factor.alias("ex_factor"),
            is_event.alias("is_event"),
            pl.when(valid).then(pl.lit("ok")).otherwise(pl.lit("invalid_pre_close")).alias("quality_status"),
        )
        return (
            base.with_columns(pl.col("ex_factor").cum_prod().over("symbol").alias("cum_factor"))
            .select(
                "symbol", pl.col("date").alias("trade_date"), "ex_factor", "cum_factor", "is_event",
                "quality_status", "previous_raw_close", "pre_close",
                pl.lit("xbx_pre_close").alias("source"),
                pl.lit(LocalXbxAdjFactorBuilder.VERSION).alias("factor_version"),
            )
            .sort(["trade_date", "symbol"])
        )

    @staticmethod
    def _write_partitions(frame: pl.DataFrame, root: Path) -> None:
        for year_frame in frame.with_columns(pl.col("trade_date").dt.year().alias("_year")).partition_by("_year", maintain_order=True):
            year = int(year_frame["_year"][0])
            path = root / f"year={year}" / "part.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            year_frame.drop("_year").write_parquet(path)

    def _publish(self, staged: Path) -> None:
        target = factor_dataset_path(self.data_dir)
        backup = target.with_name(f".adj_factor_xbx_backup_{uuid4().hex}")
        if target.exists():
            target.replace(backup)
        try:
            staged.replace(target)
        except Exception:
            if backup.exists():
                backup.replace(target)
            raise
        shutil.rmtree(backup, ignore_errors=True)
