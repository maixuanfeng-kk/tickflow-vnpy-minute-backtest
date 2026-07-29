"""Import XBX per-stock historical daily CSV files into ``kline_daily_xbx``."""
from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl


REQUIRED_COLUMNS = {"股票代码", "股票名称", "交易日期", "开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额", "总市值"}


@dataclass
class LocalDailyXbxImportSummary:
    status: str
    dry_run: bool
    source_label: str
    files_discovered: int = 0
    files_imported: int = 0
    rows_read: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    partitions_rebuilt: int = 0
    earliest_date: str | None = None
    latest_date: str | None = None
    failed_files: list[dict[str, str]] = field(default_factory=list)
    imported_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LocalDailyXbxCsvImporter:
    """Atomic, full-rebuild importer for XBX CSVs named ``sh600000.csv``."""

    def __init__(self, source_dir: Path, data_dir: Path, *, batch_size: int = 100, progress: Callable[[int, int, str], None] | None = None) -> None:
        self.source_dir = Path(source_dir)
        self.data_dir = Path(data_dir)
        self.batch_size = max(1, batch_size)
        self.progress = progress

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "user_data" / "local_daily_xbx_import.json"

    def run(self, *, dry_run: bool = False) -> LocalDailyXbxImportSummary:
        source_dir = self.source_dir.resolve()
        if not source_dir.is_dir():
            raise ValueError(f"LOCAL_DAILY_XBX_CSV_DIR 不是可访问目录: {source_dir}")
        files = sorted(source_dir.glob("*.csv"))
        summary = LocalDailyXbxImportSummary("preview" if dry_run else "running", dry_run, source_dir.name, files_discovered=len(files))
        if not files:
            raise ValueError("未找到 XBX 日K CSV 文件")
        stage = self.data_dir / ".local-daily-xbx-import-staging" / uuid4().hex
        try:
            for batch_index, start in enumerate(range(0, len(files), self.batch_size)):
                frames = [self._read_file(path, summary) for path in files[start:start + self.batch_size]]
                valid = [frame for frame in frames if frame is not None and not frame.is_empty()]
                if valid and not dry_run:
                    self._stage_by_year(pl.concat(valid, how="diagonal_relaxed"), stage, batch_index)
                self._report(min(start + self.batch_size, len(files)), len(files), "解析 XBX 日K CSV")
            if not dry_run:
                self._build_final_partitions(stage, summary)
                self._publish(stage / "final")
                summary.status = "succeeded"
                summary.imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                self._write_manifest(summary)
            return summary
        except Exception as exc:
            summary.status = "failed"
            self._record(summary.failed_files, "<import>", str(exc))
            if not dry_run:
                self._write_manifest(summary)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            try:
                stage.parent.rmdir()
            except OSError:
                pass

    def _read_file(self, path: Path, summary: LocalDailyXbxImportSummary) -> pl.DataFrame | None:
        try:
            try:
                raw = pl.read_csv(path, skip_rows=1, infer_schema_length=1_000, null_values=["", "None", "nan"], encoding="utf8")
            except Exception:
                raw = pl.read_csv(path, skip_rows=1, infer_schema_length=1_000, null_values=["", "None", "nan"], encoding="gbk")
        except Exception as exc:  # noqa: BLE001
            self._record(summary.failed_files, path.name, f"CSV 读取失败: {exc}")
            return None
        missing = REQUIRED_COLUMNS - set(raw.columns)
        if missing:
            self._record(summary.failed_files, path.name, f"缺少字段: {', '.join(sorted(missing))}")
            return None
        summary.rows_read += raw.height
        code = pl.col("股票代码").cast(pl.Utf8).str.strip_chars().str.to_lowercase()
        numeric_map = {"开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close", "前收盘价": "pre_close", "成交量": "volume", "成交额": "amount", "流通市值": "circ_mv", "总市值": "total_mv"}
        expressions = [
            pl.when(code.str.contains(r"^(sh|sz|bj)\d{6}$"))
            .then(pl.concat_str([code.str.slice(2), pl.lit("."), code.str.slice(0, 2).str.to_uppercase()]))
            .otherwise(pl.lit(None, dtype=pl.Utf8)).alias("symbol"),
            pl.col("股票名称").cast(pl.Utf8).str.strip_chars().alias("name"),
            pl.col("交易日期").cast(pl.Utf8).str.strip_chars().str.strptime(pl.Date, "%Y-%m-%d", strict=False).alias("date"),
        ]
        for source, target in numeric_map.items():
            expressions.append((pl.col(source).cast(pl.Float64, strict=False) if source in raw.columns else pl.lit(None, dtype=pl.Float64)).alias(target))
        frame = raw.with_columns(expressions)
        valid = (
            pl.col("symbol").is_not_null() & pl.col("name").is_not_null() & pl.col("date").is_not_null()
            & pl.all_horizontal(*[pl.col(item).is_not_null() for item in ("open", "high", "low", "close", "total_mv")])
            & (pl.col("open") > 0) & (pl.col("high") > 0) & (pl.col("low") > 0) & (pl.col("close") > 0)
            & (pl.col("high") >= pl.max_horizontal("open", "close", "low"))
            & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
        )
        clean = frame.filter(valid)
        summary.rows_valid += clean.height
        summary.rows_invalid += frame.height - clean.height
        if clean.is_empty():
            self._record(summary.failed_files, path.name, "没有可导入的有效数据")
            return None
        summary.files_imported += 1
        return clean

    def _stage_by_year(self, frame: pl.DataFrame, stage: Path, batch_index: int) -> None:
        dated = frame.with_columns(pl.col("date").dt.year().alias("_year"))
        for part in dated.partition_by("_year", maintain_order=True):
            year = part["_year"][0]
            output = stage / "year_batches" / f"year={year}" / f"batch={batch_index:05d}.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            part.drop("_year").write_parquet(output)

    def _build_final_partitions(self, stage: Path, summary: LocalDailyXbxImportSummary) -> None:
        year_dirs = sorted((stage / "year_batches").glob("year=*"))
        all_dates: list[str] = []
        for year_dir in year_dirs:
            frame = pl.concat([pl.read_parquet(path) for path in sorted(year_dir.glob("batch=*.parquet"))], how="diagonal_relaxed")
            for part in frame.unique(subset=["symbol", "date"], keep="last", maintain_order=True).partition_by("date", maintain_order=True):
                trade_date = part["date"][0].isoformat()
                output = stage / "final" / f"date={trade_date}" / "part.parquet"
                output.parent.mkdir(parents=True, exist_ok=True)
                part.sort("symbol").write_parquet(output)
                all_dates.append(trade_date)
                summary.partitions_rebuilt += 1
                summary.earliest_date = trade_date if summary.earliest_date is None else min(summary.earliest_date, trade_date)
                summary.latest_date = trade_date if summary.latest_date is None else max(summary.latest_date, trade_date)
            self._report(len(all_dates), 0, f"写入 XBX 日K 分区 ({year_dir.name})")
        metadata = {
            "dataset": "kline_daily_xbx", "version": 1, "market_cap_unit": "CNY",
            "st_rule": "name begins with ST, *ST, S*ST, or SST at as_of_date",
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        path = stage / "final" / "_metadata.json"
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def _publish(self, staged_final: Path) -> None:
        target = self.data_dir / "kline_daily_xbx"
        backup = target.with_name(f".kline_daily_xbx_backup_{uuid4().hex}")
        if target.exists():
            target.replace(backup)
        try:
            staged_final.replace(target)
        except Exception:
            if backup.exists():
                backup.replace(target)
            raise
        shutil.rmtree(backup, ignore_errors=True)

    def _write_manifest(self, summary: LocalDailyXbxImportSummary) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.manifest_path)

    def _report(self, current: int, total: int, stage: str) -> None:
        if self.progress:
            self.progress(current, total, stage)

    @staticmethod
    def _record(records: list[dict[str, str]], filename: str, reason: str) -> None:
        if len(records) < 100:
            records.append({"file": filename, "reason": reason})
