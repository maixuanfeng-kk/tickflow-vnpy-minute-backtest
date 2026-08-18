"""Import English-schema ETF minute CSV files into the ETF minute store."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, time
from pathlib import Path

import polars as pl

from app.tickflow.etf_datasets import ETF_MINUTE_DATASET

CANONICAL_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]


@dataclass
class LocalEtfMinuteImportSummary:
    status: str
    dry_run: bool
    source_label: str
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


class LocalEtfMinuteCsvImporter:
    """Import ``159915_SZ/202601.csv`` style UTF-8 ETF minute files."""

    def __init__(self, source_dir: Path, data_dir: Path) -> None:
        self.source_dir = Path(source_dir)
        self.data_dir = Path(data_dir)

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "user_data" / "local_etf_minute_import.json"

    def run(self, *, dry_run: bool = False) -> LocalEtfMinuteImportSummary:
        source = self.source_dir.resolve()
        if source.is_file():
            files = [source]
        elif source.is_dir():
            files = sorted(source.glob("*/[0-9]*.csv"))
            if not files and source.name.upper().endswith(("_SZ", "_SH")):
                files = sorted(source.glob("[0-9]*.csv"))
        else:
            raise ValueError(f"ETF minute source directory or file does not exist: {source}")
        summary = LocalEtfMinuteImportSummary(
            status="preview" if dry_run else "running",
            dry_run=dry_run,
            source_label=source.name,
            files_discovered=len(files),
        )
        if not files:
            raise ValueError("ETF minute source directory contains no monthly CSV files")

        frames: list[pl.DataFrame] = []
        for path in files:
            try:
                frame = self._read_file(path, summary)
            except Exception as exc:
                summary.failed_files.append({"file": str(path), "reason": str(exc)})
                continue
            if frame is not None and not frame.is_empty():
                frames.append(frame)
        if not dry_run and frames:
            self._merge(frames, summary)
            summary.status = "succeeded"
            summary.imported_at = datetime.now(UTC).isoformat(timespec="seconds")
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        elif dry_run:
            summary.status = "preview"
        else:
            summary.status = "succeeded"
        return summary

    def _read_file(self, path: Path, summary: LocalEtfMinuteImportSummary) -> pl.DataFrame | None:
        folder_code = path.parent.name.upper()
        if folder_code.endswith(("_SZ", "_SH")):
            expected_symbol = f"{folder_code[:-3]}.{folder_code[-2:]}"
        elif path.stem.upper().endswith((".SZ", ".SH")):
            expected_symbol = path.stem.upper()
        else:
            summary.skipped_files.append({"file": str(path), "reason": "invalid ETF code"})
            return None
        raw = pl.read_csv(path, encoding="utf8-lossy", infer_schema_length=1000)
        aliases = {
            "symbol": "ts_code" if "ts_code" in raw.columns else "code",
            "datetime": "trade_time" if "trade_time" in raw.columns else "datetime",
            "volume": "vol" if "vol" in raw.columns else "volume",
        }
        required = {"open", "close", "high", "low", "amount", *aliases.values()}
        missing = required - set(raw.columns)
        if missing:
            summary.failed_files.append({"file": str(path), "reason": f"missing columns: {sorted(missing)}"})
            return None
        summary.rows_read += raw.height
        frame = raw.select(
            pl.col(aliases["symbol"]).cast(pl.Utf8).str.strip_chars().alias("_source_symbol"),
            pl.col(aliases["datetime"]).cast(pl.Utf8).str.strptime(pl.Datetime("us"), strict=False).alias("datetime"),
            pl.col("open").cast(pl.Float64, strict=False),
            pl.col("high").cast(pl.Float64, strict=False),
            pl.col("low").cast(pl.Float64, strict=False),
            pl.col("close").cast(pl.Float64, strict=False),
            pl.col(aliases["volume"]).cast(pl.Float64, strict=False).alias("volume"),
            pl.col("amount").cast(pl.Float64, strict=False),
        )
        valid = (
            (pl.col("_source_symbol") == expected_symbol)
            & pl.col("datetime").is_not_null()
            & (pl.col("datetime").dt.time() <= time(15, 0))
            & pl.all_horizontal(*[pl.col(name).is_not_null() for name in CANONICAL_COLUMNS[2:]])
            & pl.all_horizontal(*[pl.col(name) > 0 for name in ("open", "high", "low", "close")])
            & pl.all_horizontal(*[pl.col(name) >= 0 for name in ("volume", "amount")])
            & (pl.col("high") >= pl.max_horizontal("open", "close", "low"))
            & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
        )
        clean = (
            frame.filter(valid)
            .with_columns(pl.lit(expected_symbol).alias("symbol"))
            .select(CANONICAL_COLUMNS)
            .unique(subset=["symbol", "datetime"], keep="last")
            .sort(["symbol", "datetime"])
        )
        summary.rows_valid += clean.height
        summary.rows_invalid += raw.height - clean.height
        if clean.is_empty():
            summary.skipped_files.append({"file": str(path), "reason": "no valid rows"})
            return None
        summary.files_imported += 1
        return clean

    def _merge(self, frames: list[pl.DataFrame], summary: LocalEtfMinuteImportSummary) -> None:
        incoming = pl.concat(frames, how="vertical_relaxed").with_columns(
            pl.col("datetime").dt.date().alias("_trade_date")
        )
        for (trade_date,), partition in incoming.partition_by("_trade_date", as_dict=True).items():
            date_text = str(trade_date)
            output = self.data_dir / ETF_MINUTE_DATASET / f"date={date_text}" / "part.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            partition = partition.drop("_trade_date")
            existing = pl.read_parquet(output) if output.exists() else pl.DataFrame(schema=partition.schema)
            merged = (
                pl.concat([existing, partition], how="vertical_relaxed")
                .unique(subset=["symbol", "datetime"], keep="last")
                .sort(["symbol", "datetime"])
            )
            summary.rows_added += max(merged.height - existing.height, 0)
            summary.rows_skipped_existing += max(partition.height - (merged.height - existing.height), 0)
            merged.write_parquet(output)
            summary.earliest_date = date_text if summary.earliest_date is None else min(summary.earliest_date, date_text)
            summary.latest_date = date_text if summary.latest_date is None else max(summary.latest_date, date_text)


def refresh_etf_minute_view(repo) -> None:
    data_dir = repo.store.data_dir.as_posix()
    repo.db.execute(
        f"""CREATE OR REPLACE VIEW kline_etf_minute AS
            SELECT * FROM read_parquet('{data_dir}/{ETF_MINUTE_DATASET}/**/*.parquet', union_by_name=true)"""
    )
