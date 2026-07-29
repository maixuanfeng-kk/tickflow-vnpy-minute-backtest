"""Import administrator-provided A-share minute CSV files into the canonical store.

This importer deliberately has no HTTP endpoint.  The source directory comes from
``LOCAL_MINUTE_CSV_DIR`` and is only consumed by the server-side CLI command.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl

logger = logging.getLogger(__name__)

CANONICAL_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
REQUIRED_SOURCE_COLUMNS = {
    "股票代码", "k线结束时间", "开盘价", "收盘价", "最高价", "最低价", "成交量", "成交额",
}
FILENAME_RE = re.compile(r"^(sh|sz|bj)(\d{6})\.csv$", re.IGNORECASE)


@dataclass
class LocalMinuteImportSummary:
    status: str
    dry_run: bool
    source_label: str
    adjustment: str = "unknown"
    volume_unit: str = "source_lot_to_share_x100"
    files_discovered: int = 0
    files_imported: int = 0
    rows_read: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    rows_added: int = 0
    rows_skipped_existing: int = 0
    earliest_date: str | None = None
    latest_date: str | None = None
    skipped_files: list[dict[str, str]] = field(default_factory=list)
    failed_files: list[dict[str, str]] = field(default_factory=list)
    imported_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LocalMinuteCsvImporter:
    """Batch importer for GBK CSV files named like ``sh600000.csv``.

    Staging is outside ``kline_minute`` so the application's glob-based repository
    cannot accidentally query data that has not been merged into a final partition.
    """

    def __init__(
        self,
        source_dir: Path,
        data_dir: Path,
        *,
        batch_size: int = 20,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.source_dir = Path(source_dir)
        self.data_dir = Path(data_dir)
        self.batch_size = max(1, batch_size)
        self.progress = progress

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "user_data" / "local_minute_import.json"

    def run(self, *, dry_run: bool = False) -> LocalMinuteImportSummary:
        source_dir = self.source_dir.resolve()
        if not source_dir.is_dir():
            raise ValueError(f"LOCAL_MINUTE_CSV_DIR 不是可访问目录: {source_dir}")

        files = sorted(path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".csv")
        summary = LocalMinuteImportSummary(
            status="preview" if dry_run else "running",
            dry_run=dry_run,
            source_label=source_dir.name,
            files_discovered=len(files),
        )
        if not files:
            raise ValueError("LOCAL_MINUTE_CSV_DIR 中没有 CSV 文件")

        stage_dir = self.data_dir / ".local-minute-import-staging" / uuid4().hex
        dates: set[str] = set()
        try:
            for batch_start in range(0, len(files), self.batch_size):
                batch = files[batch_start : batch_start + self.batch_size]
                frames: list[pl.DataFrame] = []
                for path in batch:
                    frame = self._read_file(path, summary)
                    if frame is not None and not frame.is_empty():
                        frames.append(frame)
                if frames and not dry_run:
                    self._stage_batch(pl.concat(frames, how="vertical"), stage_dir, batch_start // self.batch_size, dates)
                self._report_progress(min(batch_start + len(batch), len(files)), len(files), "解析本地分钟 CSV")

            if not dry_run:
                self._merge_staging(stage_dir, dates, summary)
                summary.status = "succeeded"
                summary.imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                self._write_manifest(summary)
            return summary
        except Exception as exc:
            summary.status = "failed"
            summary.failed_files.append({"file": "<import>", "reason": str(exc)})
            summary.imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            if not dry_run:
                self._write_manifest(summary)
            raise
        finally:
            if stage_dir.exists():
                shutil.rmtree(stage_dir, ignore_errors=True)
            parent = stage_dir.parent
            try:
                parent.rmdir()
            except OSError:
                pass

    def _read_file(self, path: Path, summary: LocalMinuteImportSummary) -> pl.DataFrame | None:
        match = FILENAME_RE.match(path.name)
        if not match:
            self._record(summary.skipped_files, path.name, "文件名必须形如 sh600000.csv / sz000001.csv / bj920000.csv")
            return None
        expected_code = f"{match.group(1).lower()}{match.group(2)}"
        try:
            raw = pl.read_csv(path, skip_rows=1, encoding="gbk", infer_schema_length=1_000)
        except Exception as exc:  # noqa: BLE001
            self._record(summary.failed_files, path.name, f"CSV 读取失败: {exc}")
            return None
        missing = REQUIRED_SOURCE_COLUMNS - set(raw.columns)
        if missing:
            self._record(summary.failed_files, path.name, f"缺少列: {', '.join(sorted(missing))}")
            return None

        summary.rows_read += raw.height
        code = pl.col("股票代码").cast(pl.Utf8).str.strip_chars().str.to_lowercase()
        symbol = pl.concat_str([
            code.str.slice(2),
            pl.lit("."),
            code.str.slice(0, 2).str.to_uppercase(),
        ])
        frame = raw.select(
            symbol.alias("symbol"),
            pl.col("k线结束时间").cast(pl.Utf8).str.strip_chars().str.strptime(
                pl.Datetime("us"), "%Y-%m-%d %H:%M:%S", strict=False
            ).alias("datetime"),
            pl.col("开盘价").cast(pl.Float64, strict=False).alias("open"),
            pl.col("最高价").cast(pl.Float64, strict=False).alias("high"),
            pl.col("最低价").cast(pl.Float64, strict=False).alias("low"),
            pl.col("收盘价").cast(pl.Float64, strict=False).alias("close"),
            # 邢不行这批分钟 CSV 的成交量单位为手；规范库统一使用股。
            (pl.col("成交量").cast(pl.Float64, strict=False) * 100).alias("volume"),
            pl.col("成交额").cast(pl.Float64, strict=False).alias("amount"),
            code.alias("_source_code"),
        )
        valid = (
            (pl.col("_source_code") == expected_code)
            & pl.col("datetime").is_not_null()
            & pl.all_horizontal(*[pl.col(name).is_not_null() for name in ("open", "high", "low", "close", "volume", "amount")])
            & (pl.col("open") > 0)
            & (pl.col("high") > 0)
            & (pl.col("low") > 0)
            & (pl.col("close") > 0)
            & (pl.col("high") >= pl.max_horizontal("open", "close", "low"))
            & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
            & (pl.col("volume") >= 0)
            & (pl.col("amount") >= 0)
        )
        clean = frame.filter(valid).select(CANONICAL_COLUMNS)
        invalid = frame.height - clean.height
        summary.rows_valid += clean.height
        summary.rows_invalid += invalid
        if invalid:
            self._record(summary.skipped_files, path.name, f"跳过 {invalid} 行无效数据")
        if clean.is_empty():
            self._record(summary.failed_files, path.name, "没有可导入的有效数据")
            return None
        summary.files_imported += 1
        return clean

    def _stage_batch(self, frame: pl.DataFrame, stage_dir: Path, batch_index: int, dates: set[str]) -> None:
        dated = frame.with_columns(pl.col("datetime").dt.date().alias("_trade_date"))
        for partition in dated.partition_by("_trade_date", maintain_order=True):
            trade_date = str(partition["_trade_date"][0])
            dates.add(trade_date)
            output = stage_dir / f"batch={batch_index:04d}" / f"date={trade_date}" / "part.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            partition.drop("_trade_date").write_parquet(output)

    def _merge_staging(self, stage_dir: Path, dates: set[str], summary: LocalMinuteImportSummary) -> None:
        for index, trade_date in enumerate(sorted(dates), start=1):
            paths = sorted(stage_dir.glob(f"batch=*/date={trade_date}/part.parquet"))
            if not paths:
                continue
            incoming = pl.concat([pl.read_parquet(path) for path in paths], how="vertical")
            output = self.data_dir / "kline_minute" / f"date={trade_date}" / "part.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            existing = pl.read_parquet(output) if output.exists() else pl.DataFrame(schema=incoming.schema)
            if not existing.is_empty():
                existing = existing.select([name for name in CANONICAL_COLUMNS if name in existing.columns])
                existing = existing.filter(pl.col("datetime").is_not_null()).unique(
                    subset=["symbol", "datetime"], keep="first", maintain_order=True
                )
            merged = pl.concat([existing, incoming], how="vertical_relaxed").unique(
                subset=["symbol", "datetime"], keep="first", maintain_order=True
            ).sort(["symbol", "datetime"])
            added = merged.height - existing.height
            summary.rows_added += added
            summary.rows_skipped_existing += incoming.height - added
            temporary = output.with_suffix(".tmp")
            merged.write_parquet(temporary)
            temporary.replace(output)
            summary.earliest_date = trade_date if summary.earliest_date is None else min(summary.earliest_date, trade_date)
            summary.latest_date = trade_date if summary.latest_date is None else max(summary.latest_date, trade_date)
            self._report_progress(index, len(dates), "合并分钟 Parquet 分区")

    def _write_manifest(self, summary: LocalMinuteImportSummary) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)

    def _report_progress(self, current: int, total: int, stage: str) -> None:
        if self.progress:
            self.progress(current, total, stage)

    @staticmethod
    def _record(records: list[dict[str, str]], filename: str, reason: str) -> None:
        # Keep the manifest bounded even when a source directory contains many bad files.
        if len(records) < 100:
            records.append({"file": filename, "reason": reason})


def refresh_minute_view(repo) -> None:
    """Refresh the in-memory DuckDB view after a successful CLI import."""
    data_dir = repo.store.data_dir.as_posix()
    repo.db.execute(
        f"""CREATE OR REPLACE VIEW kline_minute AS
            SELECT * FROM read_parquet('{data_dir}/kline_minute/**/*.parquet', union_by_name=true)"""
    )
