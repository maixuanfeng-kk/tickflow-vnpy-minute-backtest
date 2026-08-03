from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import HTTPException

import app.api.financial_import as financial_import
from app.api.financial_import import (
    FinancialImportRequest,
    _import_sync,
    _read_financial_import_status,
    import_status,
    start_import,
)
from app.services.local_financial_import import LocalFinancialCsvImporter
from app.services.pipeline_jobs import JobStore


def _write_xbx_csv(directory: Path, name: str = "sh600901_商业银行.csv") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "XBX archive row\n"
        "stock_code,statement_format,report_date,publish_date,R_revenue@xbx,R_np@xbx\n"
        "sh600901,合并,20251231,2026-03-31,100,12\n",
        encoding="utf-8",
    )
    return path


def test_local_financial_import_publishes_point_in_time_income(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_xbx_csv(source)

    summary = LocalFinancialCsvImporter(source, tmp_path / "data").run()

    assert summary.status == "succeeded"
    income = pl.read_parquet(tmp_path / "data" / "financials" / "income" / "part.parquet")
    assert income.select("symbol").to_series().to_list() == ["600901.SH"]
    assert income.select("period_end").to_series().to_list() == [date(2025, 12, 31)]
    assert json.loads((tmp_path / "data" / "user_data" / "local_financial_import.json").read_text())[
        "files_imported"
    ] == 1


def test_local_financial_import_keeps_previous_publish_when_no_valid_rows(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source = tmp_path / "source"
    _write_xbx_csv(source)
    LocalFinancialCsvImporter(source, data_dir).run()
    old_bytes = (data_dir / "financials" / "income" / "part.parquet").read_bytes()

    invalid_source = tmp_path / "invalid-source"
    invalid_source.mkdir()
    (invalid_source / "bad.csv").write_text("not a financial table\nvalue\n", encoding="utf-8")
    with pytest.raises(ValueError, match="没有可发布"):
        LocalFinancialCsvImporter(invalid_source, data_dir).run()

    assert (data_dir / "financials" / "income" / "part.parquet").read_bytes() == old_bytes


def test_financial_import_status_reports_dataset_dates_and_active_job(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    manifest_path = data_dir / "user_data" / "local_financial_import.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps({"status": "succeeded", "source_label": "stock-fin-data-xbx", "files_imported": 2}),
        encoding="utf-8",
    )
    income_dir = data_dir / "financials" / "income"
    income_dir.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["600901.SH", "000001.SZ"],
            "period_end": [date(2025, 12, 31), date(2025, 9, 30)],
            "announce_date": [date(2026, 3, 31), date(2025, 10, 31)],
        }
    ).write_parquet(income_dir / "part.parquet")

    status = _read_financial_import_status(data_dir, {"id": "job-1", "stage": "financial_import", "status": "running"})

    assert status["manifest"]["source_label"] == "stock-fin-data-xbx"
    assert status["dataset"] == {
        "rows": 2,
        "symbols": 2,
        "latest_report_date": "2025-12-31",
        "latest_publish_date": "2026-03-31",
    }
    assert status["job"]["id"] == "job-1"


@pytest.mark.asyncio
async def test_start_import_rejects_empty_and_missing_directory_before_creating_a_job() -> None:
    with pytest.raises(HTTPException, match="请输入"):
        await start_import(FinancialImportRequest(source_dir="   "), None)  # type: ignore[arg-type]
    with pytest.raises(HTTPException, match="目录不存在"):
        await start_import(FinancialImportRequest(source_dir="D:/missing-financial-source"), None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_start_financial_import_uses_local_import_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    _write_xbx_csv(source)
    store = JobStore(store_dir=tmp_path / "jobs")
    monkeypatch.setattr(financial_import, "job_store", store)

    async def no_background_import(*_args: object) -> None:
        return None

    monkeypatch.setattr(financial_import, "_run_import", no_background_import)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path / "data"))))
    )

    await start_import(FinancialImportRequest(source_dir=str(source)), request)

    assert store.get(store.active_id())["timeout_s"] == financial_import.LOCAL_IMPORT_TIMEOUT_S


@pytest.mark.asyncio
async def test_start_import_rejects_directory_without_csv(tmp_path: Path) -> None:
    source = tmp_path / "empty-source"
    source.mkdir()

    with pytest.raises(HTTPException, match="没有 CSV"):
        await start_import(FinancialImportRequest(source_dir=str(source)), None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_start_import_rejects_when_another_data_job_is_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    _write_xbx_csv(source)
    store = JobStore(store_dir=tmp_path / "jobs")
    store.create()
    monkeypatch.setattr(financial_import, "job_store", store)

    with pytest.raises(HTTPException, match="已有数据任务") as error:
        await start_import(FinancialImportRequest(source_dir=str(source)), None)  # type: ignore[arg-type]

    assert error.value.status_code == 409


def test_import_sync_releases_run_slot_before_marking_job_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    data_dir = tmp_path / "data"
    _write_xbx_csv(source)
    store = JobStore(store_dir=tmp_path / "jobs")
    job_id, _ = store.create()
    events: list[str] = []
    original_succeed = store.succeed

    def succeed_after_release(job_id: str, result: object) -> None:
        events.append("succeed")
        original_succeed(job_id, result)

    class Repo:
        def refresh_cache(self) -> None:
            events.append("refresh_cache")

    monkeypatch.setattr(financial_import, "job_store", store)
    monkeypatch.setattr(financial_import, "try_acquire_run_slot", lambda: events.append("acquire") or True)
    monkeypatch.setattr(financial_import, "release_run_slot", lambda: events.append("release"))
    monkeypatch.setattr(financial_import, "invalidate_data_cache", lambda: events.append("invalidate_cache"))
    monkeypatch.setattr(store, "succeed", succeed_after_release)

    _import_sync(job_id, source, data_dir, Repo())

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=SimpleNamespace(store=SimpleNamespace(data_dir=data_dir))))
    )
    status = import_status(request)

    assert events.index("refresh_cache") < events.index("release") < events.index("succeed")
    assert status["job"]["status"] == "succeeded"
    assert status["job"]["stage"] == "financial_import"
    assert status["job"]["progress"] == 100
    assert status["dataset"]["rows"] == 1


def test_import_sync_releases_run_slot_before_marking_job_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    _write_xbx_csv(source)
    store = JobStore(store_dir=tmp_path / "jobs")
    job_id, _ = store.create()
    events: list[str] = []
    original_fail = store.fail

    def fail_after_release(job_id: str, error: str) -> None:
        events.append("fail")
        original_fail(job_id, error)

    class FailingRepo:
        def refresh_cache(self) -> None:
            raise RuntimeError("cache refresh failed")

    monkeypatch.setattr(financial_import, "job_store", store)
    monkeypatch.setattr(financial_import, "try_acquire_run_slot", lambda: True)
    monkeypatch.setattr(financial_import, "release_run_slot", lambda: events.append("release"))
    monkeypatch.setattr(financial_import, "invalidate_data_cache", lambda: None)
    monkeypatch.setattr(store, "fail", fail_after_release)

    _import_sync(job_id, source, tmp_path / "data", FailingRepo())

    assert events.index("release") < events.index("fail")
    assert store.get(job_id)["status"] == "failed"
