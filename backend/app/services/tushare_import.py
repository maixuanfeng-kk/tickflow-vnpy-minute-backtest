"""Stream local Tushare CSV exports into isolated Parquet research datasets."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import re
import shutil
from typing import Callable
from uuid import uuid4
import zipfile

import polars as pl
import pyarrow.parquet as pq


DAILY_COLUMNS = [
    "symbol", "date", "open", "high", "low", "close", "pre_close", "volume", "amount",
    "total_mv", "circ_mv", "name", "is_st",
]
MINUTE_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]


@dataclass
class TushareImportSummary:
    dataset: str
    status: str
    dry_run: bool
    years: list[int] = field(default_factory=list)
    files_discovered: int = 0
    files_imported: int = 0
    rows_read: int = 0
    rows_valid: int = 0
    rows_invalid: int = 0
    partitions_published: int = 0
    earliest_date: str | None = None
    latest_date: str | None = None
    failed_files: list[dict[str, str]] = field(default_factory=list)
    imported_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class TushareLocalImporter:
    """Idempotent importer for the checked-in local Tushare export layout."""

    VERSION = 1

    def __init__(
        self,
        source_root: Path,
        data_dir: Path,
        *,
        batch_size: int = 100,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.source_root = Path(source_root)
        self.data_dir = Path(data_dir)
        self.batch_size = max(1, batch_size)
        self.progress = progress

    def _emit(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message, flush=True)

    def import_names(self, *, dry_run: bool = False) -> TushareImportSummary:
        result, summary = self._read_name_changes(dry_run=dry_run)
        if not dry_run:
            target = self.data_dir / "name_changes_tushare"
            self._publish_single(result, target / "part.parquet", {
                "dataset": "name_changes_tushare", "version": self.VERSION, "source": "local_tushare",
            })
            basics = self._read_stock_basics(summary)
            if basics is not None:
                basics.write_parquet(target / "stock_basic.parquet")
            summary.partitions_published = 1
            self._write_manifest("tushare_name_import.json", summary)
        return self._finish(summary, dry_run)

    def _read_stock_basics(self, summary: TushareImportSummary) -> pl.DataFrame | None:
        root = self.source_root / "A股名称信息"
        frames: list[pl.DataFrame] = []
        for path in sorted(root.glob("20??/stock_basic.csv")):
            try:
                raw = pl.read_csv(path, null_values=["", "None", "nan"])
                if "ts_code" not in raw.columns:
                    raise ValueError("missing ts_code")
                frames.append(
                    raw.with_columns(
                        pl.col("ts_code").cast(pl.Utf8).str.strip_chars().str.to_uppercase().alias("symbol"),
                        pl.lit(path.parent.name).alias("source_year"),
                    )
                )
                summary.files_discovered += 1
                summary.files_imported += 1
                summary.rows_read += raw.height
                summary.rows_valid += raw.height
            except Exception as exc:  # noqa: BLE001
                self._failure(summary, path, exc)
        if not frames:
            return None
        return pl.concat(frames, how="diagonal_relaxed").unique(
            subset=["symbol", "source_year"], keep="last"
        ).sort(["symbol", "source_year"])

    def _read_name_changes(self, *, dry_run: bool) -> tuple[pl.DataFrame, TushareImportSummary]:
        root = self.source_root / "A股名称信息"
        files = sorted(root.glob("20??/namechange.csv"))
        summary = TushareImportSummary("name_changes_tushare", "preview" if dry_run else "running", dry_run, files_discovered=len(files))
        frames: list[pl.DataFrame] = []
        for path in files:
            try:
                raw = pl.read_csv(path, null_values=["", "None", "nan"])
                required = {"ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"}
                if not required.issubset(raw.columns):
                    raise ValueError(f"missing columns: {', '.join(sorted(required - set(raw.columns)))}")
                frame = raw.select(
                    pl.col("ts_code").cast(pl.Utf8).str.strip_chars().str.to_uppercase().alias("symbol"),
                    pl.col("name").cast(pl.Utf8).str.strip_chars().alias("name"),
                    pl.col("start_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("start_date"),
                    pl.col("end_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("end_date"),
                    pl.col("ann_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("announce_date"),
                    pl.col("change_reason").cast(pl.Utf8).alias("change_reason"),
                    pl.lit(path.parent.name).alias("source_year"),
                ).filter(pl.col("symbol").str.contains(r"^\d{6}\.(SH|SZ|BJ)$") & pl.col("start_date").is_not_null())
                summary.rows_read += raw.height
                summary.rows_valid += frame.height
                summary.rows_invalid += raw.height - frame.height
                summary.files_imported += 1
                frames.append(frame)
            except Exception as exc:  # noqa: BLE001
                self._failure(summary, path, exc)
        if not frames:
            raise ValueError("no valid namechange CSV files")
        result = pl.concat(frames, how="vertical_relaxed").unique(
            subset=["symbol", "start_date", "end_date", "name"], keep="last",
        ).sort(["symbol", "start_date", "end_date"])
        summary.earliest_date = result["start_date"].min().isoformat()
        summary.latest_date = (result["end_date"].max() or date.today()).isoformat()
        return result, summary

    def import_daily(self, years: list[int], *, dry_run: bool = False) -> TushareImportSummary:
        name_changes = self._load_name_changes() if not dry_run else self._read_name_changes(dry_run=True)[0]
        root = self.source_root / "A股日K"
        files = [path for year in years for path in sorted((root / str(year)).glob("*.csv"))]
        summary = TushareImportSummary("kline_daily_tushare", "preview" if dry_run else "running", dry_run, years, len(files))
        stage = self.data_dir / ".tushare-daily-stage" / uuid4().hex
        dates: set[str] = set()
        try:
            for offset in range(0, len(files), self.batch_size):
                frames = []
                for path in files[offset:offset + self.batch_size]:
                    frame = self._read_daily(path, summary, name_changes)
                    if frame is not None and not frame.is_empty():
                        frames.append(frame)
                if frames and not dry_run:
                    self._stage_by_date(pl.concat(frames, how="vertical_relaxed"), stage, offset // self.batch_size, dates, "date")
                if (offset // self.batch_size + 1) % 10 == 0 or offset + self.batch_size >= len(files):
                    self._emit(f"daily files={min(offset + self.batch_size, len(files))}/{len(files)} valid_rows={summary.rows_valid}")
            if not summary.files_imported:
                raise ValueError("no valid daily CSV files")
            if not dry_run:
                self._publish_date_partitions(stage, dates, self.data_dir / "kline_daily_tushare", summary)
                self._write_metadata(self.data_dir / "kline_daily_tushare", {
                    "dataset": "kline_daily_tushare", "version": self.VERSION, "source": "local_tushare",
                    "market_cap_unit": "cny", "volume_unit": "shares", "amount_unit": "cny",
                    "name_rule": "point_in_time_namechange; null means no recorded name change", "st_rule": "derived from historical name",
                })
                self._write_manifest("tushare_daily_import.json", summary)
            return self._finish(summary, dry_run)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def import_minutes(self, years: list[int], *, dry_run: bool = False) -> TushareImportSummary:
        root = self.source_root / "A股分钟K"
        sources: list[tuple[int, Path, bool]] = []
        for year in years:
            directory = root / str(year)
            archive = root / f"{year}.zip"
            if archive.is_file():
                sources.append((year, archive, True))
            elif directory.is_dir():
                sources.append((year, directory, False))
        summary = TushareImportSummary("kline_minute_tushare", "preview" if dry_run else "running", dry_run, years)
        stage = self.data_dir / ".tushare-minute-stage" / uuid4().hex
        dates: set[str] = set()
        writers: dict[str, pq.ParquetWriter] = {}
        batch_number = 0
        try:
            for _, source, is_zip in sources:
                if is_zip:
                    with zipfile.ZipFile(source) as archive:
                        entries = [entry for entry in archive.infolist() if entry.filename.lower().endswith(".csv")]
                        summary.files_discovered += len(entries)
                        for month in sorted({self._minute_month_key(entry.filename) for entry in entries}):
                            month_entries = [entry for entry in entries if self._minute_month_key(entry.filename) == month]
                            for offset in range(0, len(month_entries), self.batch_size):
                                batch = [
                                    (f"{source.name}:{entry.filename}", archive.read(entry))
                                    for entry in month_entries[offset:offset + self.batch_size]
                                ]
                                self._stage_minute_batch(batch, stage, batch_number, dates, writers, summary, dry_run)
                                batch_number += 1
                                self._emit(f"minute month={month} files={summary.files_imported}/{summary.files_discovered} valid_rows={summary.rows_valid}")
                else:
                    files = sorted(source.glob("*/*.csv"))
                    summary.files_discovered += len(files)
                    for month in sorted({self._minute_month_key(path.name) for path in files}):
                        month_files = [path for path in files if self._minute_month_key(path.name) == month]
                        for offset in range(0, len(month_files), self.batch_size):
                            batch = [(str(path), path) for path in month_files[offset:offset + self.batch_size]]
                            self._stage_minute_batch(batch, stage, batch_number, dates, writers, summary, dry_run)
                            batch_number += 1
                            self._emit(f"minute month={month} files={summary.files_imported}/{summary.files_discovered} valid_rows={summary.rows_valid}")
            if not summary.files_imported:
                raise ValueError("no valid minute CSV files")
            if not dry_run:
                self._close_parquet_writers(writers)
                writers.clear()
                self._publish_date_partitions(stage, dates, self.data_dir / "kline_minute_tushare", summary)
                self._write_metadata(self.data_dir / "kline_minute_tushare", {
                    "dataset": "kline_minute_tushare", "version": self.VERSION, "source": "local_tushare",
                    "price_basis": "raw", "volume_unit": "shares", "amount_unit": "cny",
                })
                self._write_manifest("tushare_minute_import.json", summary)
            return self._finish(summary, dry_run)
        finally:
            self._close_parquet_writers(writers)
            shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _minute_month_key(name: str) -> str:
        match = re.search(r"(20\d{4})\.csv$", name)
        if not match:
            raise ValueError(f"cannot derive month from minute file: {name}")
        return match.group(1)

    def build_adjustment_factors(self, *, dry_run: bool = False) -> TushareImportSummary:
        source = self.data_dir / "kline_daily_tushare"
        files = list(source.rglob("*.parquet")) if source.exists() else []
        if not files:
            raise ValueError("missing kline_daily_tushare")
        raw = (
            pl.scan_parquet(str(source / "**" / "*.parquet"))
            .select(
                pl.col("symbol").cast(pl.Utf8), pl.col("date").cast(pl.Date),
                pl.col("close").cast(pl.Float64), pl.col("pre_close").cast(pl.Float64),
            )
            .sort(["symbol", "date"])
            .collect(engine="streaming")
        )
        if raw.group_by(["symbol", "date"]).len().filter(pl.col("len") > 1).height:
            raise ValueError("duplicate Tushare daily bars prevent factor construction")
        previous = pl.col("close").shift(1).over("symbol")
        has_previous = previous.is_not_null()
        valid = (~has_previous) | ((previous > 0) & pl.col("pre_close").is_not_null() & (pl.col("pre_close") > 0))
        result = (
            raw.with_columns(
                previous.alias("previous_raw_close"),
                pl.when(has_previous & valid).then(previous / pl.col("pre_close")).otherwise(1.0).alias("ex_factor"),
                pl.when(valid).then(pl.lit("ok")).otherwise(pl.lit("invalid_pre_close")).alias("quality_status"),
            )
            .with_columns(
                ((pl.col("ex_factor") - 1.0).abs() > 1e-9).alias("is_event"),
                pl.col("ex_factor").cum_prod().over("symbol").alias("cum_factor"),
            )
            .select(
                "symbol", pl.col("date").alias("trade_date"), "ex_factor", "cum_factor", "is_event",
                "quality_status", "previous_raw_close", "pre_close",
                pl.lit("tushare_pre_close").alias("source"), pl.lit(self.VERSION).alias("factor_version"),
            )
            .sort(["trade_date", "symbol"])
        )
        summary = TushareImportSummary(
            "adj_factor_tushare", "preview" if dry_run else "running", dry_run,
            rows_read=raw.height, rows_valid=result.filter(pl.col("quality_status") == "ok").height,
            rows_invalid=result.filter(pl.col("quality_status") != "ok").height,
            earliest_date=result["trade_date"].min().isoformat(), latest_date=result["trade_date"].max().isoformat(),
        )
        if not dry_run:
            stage = self.data_dir / ".tushare-factor-stage" / uuid4().hex
            try:
                for part in result.with_columns(pl.col("trade_date").dt.year().alias("_year")).partition_by("_year"):
                    year = part["_year"][0]
                    output = stage / f"year={year}" / "part.parquet"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    part.drop("_year").write_parquet(output)
                self._publish_directory(stage, self.data_dir / "adj_factor_tushare")
                self._write_metadata(self.data_dir / "adj_factor_tushare", {
                    "dataset": "adj_factor_tushare", "version": self.VERSION, "source_dataset": "kline_daily_tushare",
                    "method": "ex_factor = previous_raw_close / current_pre_close",
                })
                summary.partitions_published = len(list((self.data_dir / "adj_factor_tushare").glob("year=*")))
                self._write_manifest("tushare_adj_factor_import.json", summary)
            finally:
                shutil.rmtree(stage, ignore_errors=True)
        return self._finish(summary, dry_run)

    def import_financials(self, *, dry_run: bool = False) -> TushareImportSummary:
        root = self.source_root / "财报"
        years = sorted(int(path.name) for path in root.glob("20??") if path.is_dir())
        files = [path for year in years for path in sorted((root / str(year)).glob("*.csv"))]
        summary = TushareImportSummary("financial_tushare", "preview" if dry_run else "running", dry_run, years, len(files))
        stage = self.data_dir / ".tushare-financial-stage" / uuid4().hex
        income_batches = 0
        try:
            for batch_number, offset in enumerate(range(0, len(files), self.batch_size)):
                raw_frames, projected = [], []
                for path in files[offset:offset + self.batch_size]:
                    try:
                        raw = pl.read_csv(path, infer_schema_length=2_000, null_values=["", "None", "nan"], truncate_ragged_lines=True)
                        if not {"ts_code", "end_date"}.issubset(raw.columns):
                            raise ValueError("missing ts_code or end_date")
                        summary.files_imported += 1
                        summary.rows_read += raw.height
                        summary.rows_valid += raw.height
                        raw_frames.append(raw.with_columns(pl.lit(path.relative_to(root).as_posix()).alias("source_file")))
                        projected.append(self._financial_projection(raw, path.relative_to(root).as_posix()))
                    except Exception as exc:  # noqa: BLE001
                        self._failure(summary, path, exc)
                if raw_frames and not dry_run:
                    raw_path = stage / "raw" / f"batch={batch_number:05d}" / "part.parquet"
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    pl.concat(raw_frames, how="diagonal_relaxed").write_parquet(raw_path)
                if projected and not dry_run:
                    income_path = stage / "income" / f"batch={batch_number:05d}.parquet"
                    income_path.parent.mkdir(parents=True, exist_ok=True)
                    pl.concat(projected, how="vertical_relaxed").write_parquet(income_path)
                    income_batches += 1
                elif projected:
                    income_batches += 1
                if (batch_number + 1) % 25 == 0 or offset + self.batch_size >= len(files):
                    self._emit(f"financial files={min(offset + self.batch_size, len(files))}/{len(files)} valid_rows={summary.rows_valid}")
            if not income_batches:
                raise ValueError("no valid financial CSV files")
            if dry_run:
                summary.rows_invalid = summary.rows_read - summary.rows_valid
                return self._finish(summary, True)
            income = (
                pl.scan_parquet(str(stage / "income" / "*.parquet"))
                .drop_nulls(["symbol", "period_end", "announce_date"])
                .sort("source_file")
                .unique(subset=["symbol", "period_end", "announce_date"], keep="last")
                .sort(["symbol", "period_end", "announce_date"])
                .collect(engine="streaming")
            )
            summary.rows_invalid = summary.rows_read - summary.rows_valid
            summary.earliest_date = income["period_end"].min().isoformat()
            summary.latest_date = income["period_end"].max().isoformat()
            if not dry_run:
                self._publish_directory(stage / "raw", self.data_dir / "financial_tushare" / "raw")
                self._publish_single(income, self.data_dir / "financial_tushare" / "income" / "part.parquet", {
                    "dataset": "financial_tushare/income", "version": self.VERSION, "source": "local_tushare",
                    "revenue_primary": "inc_revenue", "revenue_yoy": "fi_q_sales_yoy / 100", "revenue_fallback": "inc_total_revenue", "net_profit": "inc_n_income",
                })
                summary.partitions_published = 1
                self._write_manifest("tushare_financial_import.json", summary)
            return self._finish(summary, dry_run)
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _read_daily(self, path: Path, summary: TushareImportSummary, name_changes: pl.DataFrame) -> pl.DataFrame | None:
        try:
            raw = pl.read_csv(path, null_values=["", "None", "nan"])
            required = {"ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount", "total_mv", "circ_mv"}
            if not required.issubset(raw.columns):
                raise ValueError(f"missing columns: {', '.join(sorted(required - set(raw.columns)))}")
            summary.files_imported += 1
            summary.rows_read += raw.height
            frame = raw.select(
                pl.col("ts_code").cast(pl.Utf8).str.strip_chars().str.to_uppercase().alias("symbol"),
                pl.col("trade_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("date"),
                *[pl.col(column).cast(pl.Float64, strict=False).alias(target) for column, target in (
                    ("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"), ("pre_close", "pre_close"),
                    ("vol", "volume"), ("amount", "amount"), ("total_mv", "total_mv"), ("circ_mv", "circ_mv"),
                )],
            ).with_columns(
                (pl.col("volume") * 100).alias("volume"),
                (pl.col("amount") * 1_000).alias("amount"),
                (pl.col("total_mv") * 10_000).alias("total_mv"),
                (pl.col("circ_mv") * 10_000).alias("circ_mv"),
            )
            frame = frame.sort(["symbol", "date"]).join_asof(
                name_changes.select("symbol", "name", "start_date", "end_date").sort(["symbol", "start_date"]),
                left_on="date", right_on="start_date", by="symbol", strategy="backward",
            ).with_columns(
                pl.when(pl.col("end_date").is_null() | (pl.col("date") <= pl.col("end_date")))
                .then(pl.col("name"))
                .otherwise(pl.lit(None, dtype=pl.Utf8)).alias("name"),
            ).with_columns(
                pl.col("name").fill_null("").str.to_uppercase().str.replace_all(" ", "").str.starts_with("*ST").alias("_star_st"),
                pl.col("name").fill_null("").str.to_uppercase().str.replace_all(" ", "").str.starts_with("ST").alias("_st"),
                pl.col("name").fill_null("").str.to_uppercase().str.replace_all(" ", "").str.starts_with("S*ST").alias("_s_star_st"),
                pl.col("name").fill_null("").str.to_uppercase().str.replace_all(" ", "").str.starts_with("SST").alias("_sst"),
            ).with_columns(
                (pl.col("_star_st") | pl.col("_st") | pl.col("_s_star_st") | pl.col("_sst") | pl.col("name").fill_null("").str.contains("退")).alias("is_st"),
            ).drop(["start_date", "end_date", "_star_st", "_st", "_s_star_st", "_sst"])
            valid = (
                pl.col("symbol").str.contains(r"^\d{6}\.(SH|SZ|BJ)$") & pl.col("date").is_not_null()
                & pl.all_horizontal(*[pl.col(name).is_not_null() & (pl.col(name) > 0) for name in ("open", "high", "low", "close", "pre_close", "total_mv")])
                & (pl.col("high") >= pl.max_horizontal("open", "close", "low"))
                & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
            )
            clean = frame.filter(valid).select(DAILY_COLUMNS)
            summary.rows_valid += clean.height
            summary.rows_invalid += frame.height - clean.height
            return clean
        except Exception as exc:  # noqa: BLE001
            self._failure(summary, path, exc)
            return None

    def _stage_minute_batch(self, batch, stage: Path, batch_number: int, dates: set[str], writers: dict[str, pq.ParquetWriter], summary: TushareImportSummary, dry_run: bool) -> None:
        frames = []
        for label, payload in batch:
            try:
                raw = pl.read_csv(
                    BytesIO(payload) if isinstance(payload, bytes) else payload,
                    null_values=["", "None", "nan"],
                    schema_overrides={
                        "open": pl.Float64,
                        "close": pl.Float64,
                        "high": pl.Float64,
                        "low": pl.Float64,
                        "vol": pl.Float64,
                        "amount": pl.Float64,
                    },
                )
                required = {"ts_code", "trade_time", "open", "close", "high", "low", "vol", "amount"}
                if not required.issubset(raw.columns):
                    raise ValueError(f"missing columns: {', '.join(sorted(required - set(raw.columns)))}")
                frame = raw.select(
                    pl.col("ts_code").cast(pl.Utf8).str.strip_chars().str.to_uppercase().alias("symbol"),
                    pl.col("trade_time").cast(pl.Utf8).str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S", strict=False).alias("datetime"),
                    *[pl.col(column).cast(pl.Float64, strict=False).alias(target) for column, target in (
                        ("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"), ("vol", "volume"), ("amount", "amount"),
                    )],
                )
                valid = (
                    pl.col("symbol").str.contains(r"^\d{6}\.(SH|SZ|BJ)$") & pl.col("datetime").is_not_null()
                    & pl.all_horizontal(*[pl.col(name).is_not_null() & (pl.col(name) > 0) for name in ("open", "high", "low", "close")])
                    & pl.all_horizontal(pl.col("volume").is_not_null() & (pl.col("volume") >= 0), pl.col("amount").is_not_null() & (pl.col("amount") >= 0))
                    & (pl.col("high") >= pl.max_horizontal("open", "close", "low"))
                    & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
                )
                clean = frame.filter(valid).select(MINUTE_COLUMNS)
                summary.files_imported += 1
                summary.rows_read += raw.height
                summary.rows_valid += clean.height
                summary.rows_invalid += raw.height - clean.height
                if not clean.is_empty():
                    frames.append(clean)
            except Exception as exc:  # noqa: BLE001
                self._failure(summary, label, exc)
        if frames and not dry_run:
            dated = pl.concat(frames, how="vertical_relaxed").with_columns(pl.col("datetime").dt.date().alias("_date"))
            for part in dated.partition_by("_date", maintain_order=True):
                day = part["_date"][0].isoformat()
                output = stage / f"date={day}" / "part.parquet"
                output.parent.mkdir(parents=True, exist_ok=True)
                table = part.drop("_date").to_arrow()
                writer = writers.get(day)
                if writer is None:
                    writer = pq.ParquetWriter(output, table.schema, compression="zstd")
                    writers[day] = writer
                writer.write_table(table)
                dates.add(day)

    @staticmethod
    def _close_parquet_writers(writers: dict[str, pq.ParquetWriter]) -> None:
        for writer in writers.values():
            writer.close()

    @staticmethod
    def _financial_projection(raw: pl.DataFrame, source_file: str) -> pl.DataFrame:
        def column_or_null(name: str) -> pl.Expr:
            return pl.col(name).cast(pl.Float64, strict=False) if name in raw.columns else pl.lit(None, dtype=pl.Float64)

        announce = "inc_ann_date" if "inc_ann_date" in raw.columns else "bs_ann_date"
        return raw.select(
            pl.col("ts_code").cast(pl.Utf8).str.strip_chars().str.to_uppercase().alias("symbol"),
            pl.col("end_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("period_end"),
            pl.col(announce).cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("announce_date"),
            pl.coalesce([column_or_null("inc_revenue"), column_or_null("inc_total_revenue")]).alias("revenue"),
            (column_or_null("fi_q_sales_yoy") / 100).alias("revenue_yoy"),
            pl.when(column_or_null("inc_revenue").is_not_null()).then(pl.lit("inc_revenue"))
            .when(column_or_null("inc_total_revenue").is_not_null()).then(pl.lit("inc_total_revenue"))
            .otherwise(pl.lit(None, dtype=pl.Utf8)).alias("revenue_source"),
            column_or_null("inc_n_income").alias("net_income"),
            (pl.col("inc_comp_type").cast(pl.Utf8) if "inc_comp_type" in raw.columns else pl.lit(None, dtype=pl.Utf8)).alias("statement_format"),
            pl.lit(source_file).alias("source_file"),
        )

    @staticmethod
    def _is_st_name(name: str | None) -> bool:
        normalized = (name or "").strip().upper().replace(" ", "")
        return normalized.startswith(("*ST", "ST", "S*ST", "SST")) or "退" in normalized

    def _load_name_changes(self) -> pl.DataFrame:
        path = self.data_dir / "name_changes_tushare" / "part.parquet"
        if not path.exists():
            raise ValueError("missing name_changes_tushare; import names before daily data")
        return pl.read_parquet(path)

    def _stage_by_date(self, frame: pl.DataFrame, stage: Path, batch_number: int, dates: set[str], date_column: str) -> None:
        dated = frame.with_columns((pl.col(date_column).dt.date() if date_column == "datetime" else pl.col(date_column)).alias("_date"))
        for part in dated.partition_by("_date", maintain_order=True):
            day = part["_date"][0].isoformat()
            dates.add(day)
            output = stage / f"date={day}" / f"batch={batch_number:06d}.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            part.drop("_date").write_parquet(output)

    def _publish_date_partitions(self, stage: Path, dates: set[str], target: Path, summary: TushareImportSummary) -> None:
        for day in sorted(dates):
            output = target / f"date={day}" / "part.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(".tmp")
            direct_part = stage / f"date={day}" / "part.parquet"
            if direct_part.is_file():
                direct_part.replace(temporary)
                temporary.replace(output)
            else:
                parts = sorted((stage / f"date={day}").glob("batch=*.parquet"))
                if not parts:
                    continue
                frame = pl.concat([pl.read_parquet(path) for path in parts], how="vertical_relaxed")
                key = ["symbol", "datetime"] if "datetime" in frame.columns else ["symbol", "date"]
                frame.unique(subset=key, keep="last").sort(key).write_parquet(temporary)
                temporary.replace(output)
            summary.partitions_published += 1
            summary.earliest_date = day if summary.earliest_date is None else min(summary.earliest_date, day)
            summary.latest_date = day if summary.latest_date is None else max(summary.latest_date, day)

    def _publish_single(self, frame: pl.DataFrame, output: Path, metadata: dict) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".tmp")
        frame.write_parquet(temporary)
        temporary.replace(output)
        self._write_metadata(output.parent, metadata)

    def _publish_directory(self, staged: Path, target: Path) -> None:
        backup = target.with_name(f".{target.name}.backup.{uuid4().hex}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.replace(backup)
        try:
            staged.replace(target)
        except Exception:
            if backup.exists():
                backup.replace(target)
            raise
        shutil.rmtree(backup, ignore_errors=True)

    @staticmethod
    def _write_metadata(directory: Path, metadata: dict) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "_metadata.tmp"
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(directory / "_metadata.json")

    def _write_manifest(self, filename: str, summary: TushareImportSummary) -> None:
        path = self.data_dir / "user_data" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _failure(summary: TushareImportSummary, source: object, exc: Exception) -> None:
        if len(summary.failed_files) < 100:
            summary.failed_files.append({"file": str(source), "reason": str(exc)})

    @staticmethod
    def _finish(summary: TushareImportSummary, dry_run: bool) -> TushareImportSummary:
        summary.status = "preview" if dry_run else "succeeded"
        if not dry_run:
            summary.imported_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        return summary
