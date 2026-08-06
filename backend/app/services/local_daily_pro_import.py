"""Import local annual daily CSV files into the dedicated research daily store."""
from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl


SOURCE_COLUMNS = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount", "total_mv", "circ_mv"}


@dataclass
class LocalDailyProImportSummary:
    status: str
    dry_run: bool
    source_label: str
    years: list[int]
    files_discovered: int = 0
    files_imported: int = 0
    rows_read: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    partitions_rebuilt: int = 0
    earliest_date: str | None = None
    latest_date: str | None = None
    skipped_files: list[dict[str, str]] = field(default_factory=list)
    failed_files: list[dict[str, str]] = field(default_factory=list)
    imported_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LocalDailyProCsvImporter:
    """Idempotent importer for ``YYYY/000001_SZ.csv`` style daily data.

    Incoming data is staged by date.  On a successful non-dry run every affected
    date partition is rebuilt entirely from the source, so revised source files
    replace rather than silently coexist with older observations.
    """

    def __init__(
        self,
        source_dir: Path,
        data_dir: Path,
        *,
        years: Iterable[int] | None = None,
        batch_size: int = 100,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self.source_dir = Path(source_dir)
        self.data_dir = Path(data_dir)
        self.years = sorted(set(years or self._discover_years()))
        self.batch_size = max(1, batch_size)
        self.progress = progress

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "user_data" / "local_daily_pro_import.json"

    @staticmethod
    def schema_document() -> dict[str, object]:
        return {
            "dataset": "kline_daily_tushare",
            "version": 1,
            "source": "local annual daily CSV",
            "units": {
                "volume": "shares (normalised from source vol lots x100)",
                "amount": "CNY (normalised from source amount thousand-CNY x1000)",
                "vol": "source lots", "amount_source": "source thousand-CNY",
                "total_mv": "CNY (normalised from source ten-thousand CNY x10000)",
                "circ_mv": "CNY (normalised from source ten-thousand CNY x10000)",
                "total_share": "ten-thousand shares", "float_share": "ten-thousand shares",
            },
        }

    def run(self, *, dry_run: bool = False) -> LocalDailyProImportSummary:
        source_dir = self.source_dir.resolve()
        files = self._files(source_dir)
        summary = LocalDailyProImportSummary(
            status="preview" if dry_run else "running",
            dry_run=dry_run,
            source_label=source_dir.name,
            years=self.years,
            files_discovered=len(files),
        )
        if not files:
            raise ValueError("未找到指定年份的专业日K CSV 文件")

        if not dry_run:
            self._migrate_legacy_dataset()
        stage_root = self.data_dir / ".local-daily-pro-import-staging" / uuid4().hex
        dates: set[str] = set()
        try:
            for batch_index, batch_start in enumerate(range(0, len(files), self.batch_size)):
                frames = [self._read_file(path, summary) for path in files[batch_start:batch_start + self.batch_size]]
                valid_frames = [frame for frame in frames if frame is not None and not frame.is_empty()]
                if valid_frames and not dry_run:
                    self._stage_batch(pl.concat(valid_frames, how="vertical_relaxed"), stage_root, batch_index, dates)
                self._report(min(batch_start + self.batch_size, len(files)), len(files), "解析专业日K CSV")
            if not dry_run:
                self._replace_partitions(stage_root, dates, summary)
                self._write_metadata(summary)
                summary.status = "succeeded"
                summary.imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                self._write_manifest(summary)
            return summary
        except Exception as exc:
            summary.status = "failed"
            self._record(summary.failed_files, "<import>", str(exc))
            if not dry_run:
                summary.imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
                self._write_manifest(summary)
            raise
        finally:
            shutil.rmtree(stage_root, ignore_errors=True)
            try:
                stage_root.parent.rmdir()
            except OSError:
                pass

    def _discover_years(self) -> list[int]:
        if not self.source_dir.exists():
            return []
        return sorted(int(path.name) for path in self.source_dir.iterdir() if path.is_dir() and re.fullmatch(r"\d{4}", path.name))

    def _files(self, source_dir: Path) -> list[Path]:
        if not source_dir.is_dir():
            raise ValueError(f"LOCAL_DAILY_PRO_CSV_DIR 不是可访问目录: {source_dir}")
        files: list[Path] = []
        for year in self.years:
            folder = source_dir / str(year)
            if not folder.is_dir():
                self._record([], str(year), "年份目录不存在")
                continue
            files.extend(sorted(folder.glob("*.csv")))
        return files

    def _read_file(self, path: Path, summary: LocalDailyProImportSummary) -> pl.DataFrame | None:
        try:
            raw = pl.read_csv(path, infer_schema_length=1_000, null_values=["", "None", "nan"])
        except Exception as exc:  # noqa: BLE001
            self._record(summary.failed_files, path.name, f"CSV 读取失败: {exc}")
            return None
        missing = SOURCE_COLUMNS - set(raw.columns)
        if missing:
            self._record(summary.failed_files, path.name, f"缺少字段: {', '.join(sorted(missing))}")
            return None
        summary.rows_read += raw.height
        numeric = [name for name in raw.columns if name not in {"ts_code", "trade_date"}]
        typed = raw.with_columns(
            pl.col("ts_code").cast(pl.Utf8).str.strip_chars().str.to_uppercase(),
            pl.col("trade_date").cast(pl.Utf8).str.strip_chars(),
            *[pl.col(name).cast(pl.Float64, strict=False) for name in numeric],
        )
        frame = typed.with_columns(
            pl.col("ts_code").alias("symbol"),
            pl.col("trade_date").str.strptime(pl.Date, "%Y%m%d", strict=False).alias("date"),
            (pl.col("vol") * 100).alias("volume"),
            (pl.col("amount") * 1_000).alias("amount_cny"),
            pl.col("amount").alias("amount_source"),
            (pl.col("total_mv") * 10_000).alias("total_mv_cny"),
            (pl.col("circ_mv") * 10_000).alias("circ_mv_cny"),
        ).drop("amount", "total_mv", "circ_mv").rename({
            "amount_cny": "amount", "total_mv_cny": "total_mv", "circ_mv_cny": "circ_mv",
        })
        valid = (
            pl.col("symbol").str.contains(r"^\d{6}\.(SH|SZ|BJ)$")
            & pl.col("date").is_not_null()
            & pl.all_horizontal(*[pl.col(name).is_not_null() for name in ("open", "high", "low", "close")])
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

    def _stage_batch(self, frame: pl.DataFrame, stage_root: Path, batch_index: int, dates: set[str]) -> None:
        for partition in frame.partition_by("date", maintain_order=True):
            trade_date = partition["date"][0].isoformat()
            dates.add(trade_date)
            output = stage_root / f"date={trade_date}" / f"batch={batch_index:05d}.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            partition.write_parquet(output)

    def _replace_partitions(self, stage_root: Path, dates: set[str], summary: LocalDailyProImportSummary) -> None:
        output_root = self.data_dir / "kline_daily_tushare"
        for index, trade_date in enumerate(sorted(dates), start=1):
            paths = sorted((stage_root / f"date={trade_date}").glob("batch=*.parquet"))
            if not paths:
                continue
            merged = pl.concat([pl.read_parquet(path) for path in paths], how="vertical_relaxed").unique(
                subset=["symbol", "date"], keep="last", maintain_order=True
            ).sort("symbol")
            directory = output_root / f"date={trade_date}"
            directory.mkdir(parents=True, exist_ok=True)
            temporary = directory / "part.tmp"
            final = directory / "part.parquet"
            merged.write_parquet(temporary)
            temporary.replace(final)
            summary.partitions_rebuilt += 1
            summary.earliest_date = trade_date if summary.earliest_date is None else min(summary.earliest_date, trade_date)
            summary.latest_date = trade_date if summary.latest_date is None else max(summary.latest_date, trade_date)
            self._report(index, len(dates), "重建专业日K Parquet 分区")

    def _migrate_legacy_dataset(self) -> None:
        legacy = self.data_dir / "kline_daily_pro"
        target = self.data_dir / "kline_daily_tushare"
        if legacy.exists() and not target.exists():
            legacy.replace(target)

    def _write_metadata(self, summary: LocalDailyProImportSummary) -> None:
        metadata = {**self.schema_document(), "years": summary.years, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        path = self.data_dir / "kline_daily_tushare" / "_metadata.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _write_manifest(self, summary: LocalDailyProImportSummary) -> None:
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
