from __future__ import annotations

import json
from types import SimpleNamespace

import polars as pl
import pytest

from app.services.local_minute_import import LocalMinuteCsvImporter, LocalMinuteParquetImporter


def _write_source(path, *, close: str = "10.10") -> None:
    path.write_text(
        "本文件由测试数据生成\n"
        "股票代码,k线结束时间,开盘价,收盘价,最高价,最低价,成交量,成交额\n"
        f"sh600000,2026-01-05 09:30:00,10.00,{close},10.20,9.90,123,123000.0\n"
        "sh600000,2026-01-05 09:31:00,10.10,10.20,10.30,10.00,456,456000.0\n",
        encoding="gbk",
    )


def test_importer_normalizes_gbk_csv_and_preserves_existing_rows(tmp_path) -> None:
    source = tmp_path / "source"
    data_dir = tmp_path / "data"
    source.mkdir()
    csv_path = source / "sh600000.csv"
    _write_source(csv_path)

    summary = LocalMinuteCsvImporter(source, data_dir, batch_size=1).run()

    output = data_dir / "kline_minute" / "date=2026-01-05" / "part.parquet"
    frame = pl.read_parquet(output)
    assert summary.status == "succeeded"
    assert summary.rows_added == 2
    assert frame["symbol"].to_list() == ["600000.SH", "600000.SH"]
    assert frame["volume"].to_list() == [12300.0, 45600.0]
    assert frame["amount"].to_list() == [123000.0, 456000.0]

    # A later import of the same primary keys must fill nothing and never overwrite.
    _write_source(csv_path, close="99.99")
    second = LocalMinuteCsvImporter(source, data_dir, batch_size=1).run()
    after = pl.read_parquet(output)
    assert second.rows_added == 0
    assert after["close"].to_list() == [10.1, 10.2]

    manifest = json.loads((data_dir / "user_data" / "local_minute_import.json").read_text("utf-8"))
    assert manifest["adjustment"] == "unknown"
    assert str(source) not in json.dumps(manifest, ensure_ascii=False)


def test_importer_dry_run_does_not_write_and_reports_invalid_files(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_source(source / "sh600000.csv")
    (source / "notes.csv").write_text("hello\n", encoding="gbk")

    summary = LocalMinuteCsvImporter(source, tmp_path / "data").run(dry_run=True)

    assert summary.status == "preview"
    assert summary.rows_valid == 2
    assert summary.skipped_files[0]["file"] == "notes.csv"
    assert not (tmp_path / "data" / "kline_minute").exists()


def test_parquet_importer_converts_single_symbol_files_to_date_partitions(tmp_path) -> None:
    source = tmp_path / "source"
    data_dir = tmp_path / "data"
    source.mkdir()
    pl.DataFrame({
        "ts_code": ["600000.SH", "600000.SH"],
        "trade_time": ["2026-01-05 09:30:00", "2026-01-05 09:31:00"],
        "open": [10.0, 10.1],
        "high": [10.2, 10.3],
        "low": [9.9, 10.0],
        "close": [10.1, 10.2],
        "vol": [12_300, 45_600],
        "amount": [123_000.0, 456_000.0],
    }).write_parquet(source / "600000_SH.parquet")

    summary = LocalMinuteParquetImporter(source, data_dir, batch_size=1).run()

    output = data_dir / "kline_minute" / "date=2026-01-05" / "part.parquet"
    frame = pl.read_parquet(output)
    assert summary.status == "succeeded"
    assert summary.volume_unit == "share"
    assert summary.rows_added == 2
    assert frame["symbol"].to_list() == ["600000.SH", "600000.SH"]
    assert frame["volume"].to_list() == [12_300.0, 45_600.0]

    pl.DataFrame({
        "ts_code": ["600000.SH"],
        "trade_time": ["2026-01-05 09:30:00"],
        "open": [99.0], "high": [99.0], "low": [99.0], "close": [99.0],
        "vol": [100], "amount": [9_900.0],
    }).write_parquet(source / "600000_SH.parquet")
    second = LocalMinuteParquetImporter(source, data_dir, batch_size=1).run()

    assert second.rows_added == 0
    assert pl.read_parquet(output)["close"].to_list() == [10.1, 10.2]


@pytest.mark.asyncio
async def test_parquet_import_endpoint_starts_pipeline_job(monkeypatch, tmp_path) -> None:
    from app.api import kline as kline_api

    source = tmp_path / "source"
    source.mkdir()
    scheduled = []

    class JobStore:
        def create(self, *, timeout_s):
            assert timeout_s > 0
            return "import-job", True

    def capture(coroutine):
        scheduled.append(coroutine)
        coroutine.close()

    monkeypatch.setattr("app.services.pipeline_jobs.job_store", JobStore())
    monkeypatch.setattr("asyncio.create_task", capture)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=object())))

    result = await kline_api.import_local_minute_parquet(request, {"source_dir": str(source)})

    assert result == {"status": "started", "job_id": "import-job"}
    assert len(scheduled) == 1
