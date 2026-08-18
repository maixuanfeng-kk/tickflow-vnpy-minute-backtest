"""Import local ETF daily CSVs and minute ZIPs into Tushare-named parquet datasets."""
from __future__ import annotations

import argparse
import io
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import polars as pl

from app.tickflow.etf_datasets import ETF_DAILY_DATASET, ETF_MINUTE_DATASET


DAILY_COLUMNS = [
    "symbol", "date", "open", "high", "low", "close", "pre_close", "volume", "amount",
]
MINUTE_COLUMNS = [
    "symbol", "datetime", "open", "high", "low", "close", "volume", "amount",
]
MINUTE_ENTRY = re.compile(r"(?:^|/)(\d{8})_1min/[^/]+\.csv$")


def _write_partition(frame: pl.DataFrame, output: Path, keys: list[str]) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = pl.read_parquet(output) if output.exists() else pl.DataFrame(schema=frame.schema)
    merged = (
        pl.concat([existing, frame], how="diagonal_relaxed")
        .unique(subset=keys, keep="last")
        .sort(keys)
    )
    temporary = output.with_name(output.name + ".tmp")
    merged.write_parquet(temporary)
    temporary.replace(output)
    return merged.height


def _daily_frame(path: Path) -> pl.DataFrame:
    raw = pl.read_csv(path, infer_schema_length=1000)
    required = {"ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"}
    if missing := required - set(raw.columns):
        raise ValueError(f"{path.name} missing columns: {sorted(missing)}")
    frame = raw.select(
        pl.col("ts_code").cast(pl.Utf8).str.strip_chars().alias("symbol"),
        pl.col("trade_date").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False).alias("date"),
        *[pl.col(column).cast(pl.Float64, strict=False) for column in ("open", "high", "low", "close", "pre_close")],
        pl.col("vol").cast(pl.Float64, strict=False).alias("volume"),
        pl.col("amount").cast(pl.Float64, strict=False),
    )
    return frame.filter(
        pl.col("symbol").is_not_null()
        & pl.col("date").is_not_null()
        & pl.all_horizontal(*[pl.col(name).is_not_null() & (pl.col(name) > 0) for name in ("open", "high", "low", "close", "pre_close")])
        & pl.all_horizontal(*[pl.col(name).is_not_null() & (pl.col(name) >= 0) for name in ("volume", "amount")])
        & (pl.col("high") >= pl.max_horizontal("open", "close", "low"))
        & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
    ).select(DAILY_COLUMNS)


def import_daily(source: Path, data_dir: Path) -> tuple[int, int]:
    frames = [_daily_frame(path) for path in sorted(source.glob("*.csv"))]
    if not frames:
        raise ValueError(f"no daily CSV files found in {source}")
    daily = pl.concat(frames, how="vertical_relaxed").unique(subset=["symbol", "date"], keep="last")
    rows = 0
    for partition in daily.partition_by("date"):
        trade_date = partition["date"][0].isoformat()
        rows += _write_partition(
            partition, data_dir / ETF_DAILY_DATASET / f"date={trade_date}" / "part.parquet", ["symbol", "date"]
        )
    return len(frames), rows


def _minute_frame(content: bytes, source_name: str) -> pl.DataFrame:
    raw = pl.read_csv(io.BytesIO(content), infer_schema_length=500)
    required = {"datetime", "code", "open", "high", "low", "close", "volume", "amount"}
    if missing := required - set(raw.columns):
        raise ValueError(f"{source_name} missing columns: {sorted(missing)}")
    frame = raw.select(
        pl.col("code").cast(pl.Utf8).str.strip_chars().alias("symbol"),
        pl.col("datetime").cast(pl.Utf8).str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S", strict=False).alias("datetime"),
        *[pl.col(column).cast(pl.Float64, strict=False) for column in ("open", "high", "low", "close", "volume", "amount")],
    )
    return frame.filter(
        pl.col("symbol").is_not_null()
        & pl.col("datetime").is_not_null()
        & pl.all_horizontal(*[pl.col(name).is_not_null() & (pl.col(name) > 0) for name in ("open", "high", "low", "close")])
        & pl.all_horizontal(*[pl.col(name).is_not_null() & (pl.col(name) >= 0) for name in ("volume", "amount")])
        & (pl.col("high") >= pl.max_horizontal("open", "close", "low"))
        & (pl.col("low") <= pl.min_horizontal("open", "close", "high"))
    ).select(MINUTE_COLUMNS)


def import_minutes(source: Path, data_dir: Path) -> tuple[int, int, int]:
    archives = sorted(source.glob("*.zip"))
    if not archives:
        raise ValueError(f"no minute ZIP files found in {source}")
    csv_files = 0
    rows = 0
    days = 0
    for archive in archives:
        with zipfile.ZipFile(archive) as bundle:
            entries_by_day: dict[str, list[zipfile.ZipInfo]] = defaultdict(list)
            for entry in bundle.infolist():
                match = MINUTE_ENTRY.search(entry.filename)
                if match and not entry.filename.startswith("__MACOSX/"):
                    entries_by_day[match.group(1)].append(entry)
            for compact_date, entries in sorted(entries_by_day.items()):
                frames = [_minute_frame(bundle.read(entry), entry.filename) for entry in entries]
                minute = pl.concat(frames, how="vertical_relaxed").unique(subset=["symbol", "datetime"], keep="last")
                trade_date = f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:]}"
                rows += _write_partition(
                    minute,
                    data_dir / ETF_MINUTE_DATASET / f"date={trade_date}" / "part.parquet",
                    ["symbol", "datetime"],
                )
                csv_files += len(entries)
                days += 1
    return len(archives), csv_files, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-source", type=Path, required=True)
    parser.add_argument("--minute-source", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()

    daily_files, daily_rows = import_daily(args.daily_source, args.data_dir)
    archives, minute_files, minute_rows = import_minutes(args.minute_source, args.data_dir)
    print(
        f"daily: {daily_files} files, {daily_rows} rows; "
        f"minute: {archives} archives, {minute_files} files, {minute_rows} rows"
    )


if __name__ == "__main__":
    main()
