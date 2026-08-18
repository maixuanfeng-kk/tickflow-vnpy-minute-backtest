from pathlib import Path

import polars as pl

from app.services.local_etf_minute_import import LocalEtfMinuteCsvImporter
from app.tickflow.etf_datasets import ETF_MINUTE_DATASET


def _write_source(root: Path, *, code: str = "159915.SZ") -> None:
    folder = root / "159915_SZ"
    folder.mkdir(parents=True)
    (folder / "202607.csv").write_text(
        "ts_code,trade_time,open,close,high,low,vol,amount\n"
        f"{code},2026-07-01 15:00:00,3.9,3.9,3.9,3.9,100,390\n"
        f"{code},2026-07-01 15:06:00,3.9,3.9,3.9,3.9,100,390\n",
        encoding="utf-8",
    )


def test_imports_english_etf_csv_and_filters_post_close(tmp_path: Path) -> None:
    source = tmp_path / "source"
    data = tmp_path / "data"
    _write_source(source)

    summary = LocalEtfMinuteCsvImporter(source, data).run()

    assert summary.rows_valid == 1
    output = data / ETF_MINUTE_DATASET / "date=2026-07-01" / "part.parquet"
    frame = pl.read_parquet(output)
    assert frame.select("symbol", "datetime", "volume").to_dicts() == [
        {
            "symbol": "159915.SZ",
            "datetime": __import__("datetime").datetime(2026, 7, 1, 15, 0),
            "volume": 100.0,
        }
    ]


def test_import_is_idempotent_and_rejects_wrong_code(tmp_path: Path) -> None:
    source = tmp_path / "source"
    data = tmp_path / "data"
    _write_source(source, code="159916.SZ")

    first = LocalEtfMinuteCsvImporter(source, data).run()
    second = LocalEtfMinuteCsvImporter(source, data).run()

    assert first.rows_valid == 0
    assert second.rows_valid == 0
    assert list((data / ETF_MINUTE_DATASET).rglob("*.parquet")) == []


def test_imports_flat_download_csv_schema(tmp_path: Path) -> None:
    source = tmp_path / "159915.SZ.csv"
    data = tmp_path / "data"
    source.write_text(
        "datetime,code,name,open,close,high,low,volume,amount,pct_chg,amplitude\n"
        "2025-01-02 09:30:00,159915.SZ,创业板ETF,2.1,2.1,2.1,2.1,100,210,0,0\n",
        encoding="utf-8",
    )

    summary = LocalEtfMinuteCsvImporter(source, data).run()

    assert summary.rows_valid == 1
    frame = pl.read_parquet(data / ETF_MINUTE_DATASET / "date=2025-01-02" / "part.parquet")
    assert frame.select("symbol", "volume").to_dicts() == [{"symbol": "159915.SZ", "volume": 100.0}]


def test_import_keeps_zero_volume_minute_with_valid_prices(tmp_path: Path) -> None:
    source = tmp_path / "159915.SZ.csv"
    data = tmp_path / "data"
    source.write_text(
        "datetime,code,name,open,close,high,low,volume,amount,pct_chg,amplitude\n"
        "2025-01-02 09:30:00,159915.SZ,创业板ETF,2.1,2.1,2.1,2.1,0,0,0,0\n",
        encoding="utf-8",
    )

    summary = LocalEtfMinuteCsvImporter(source, data).run()

    assert summary.rows_valid == 1
    frame = pl.read_parquet(data / ETF_MINUTE_DATASET / "date=2025-01-02" / "part.parquet")
    assert frame.select("volume", "amount").to_dicts() == [{"volume": 0.0, "amount": 0.0}]
