from pathlib import Path

import polars as pl

from app.services.local_etf_minute_import import LocalEtfMinuteCsvImporter


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
    output = data / "kline_etf_minute" / "date=2026-07-01" / "part.parquet"
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
    assert list((data / "kline_etf_minute").rglob("*.parquet")) == []
