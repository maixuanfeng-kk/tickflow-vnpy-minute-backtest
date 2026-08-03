from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import daily_pro_import
from app.services.pipeline_jobs import JobStore


def _write_tushare_daily_csv(path: Path) -> None:
    path.write_text(
        "ts_code,trade_date,open,high,low,close,pre_close,vol,amount,total_mv,circ_mv\n"
        "000001.SZ,20260105,11.42,11.51,11.41,11.50,11.41,875491.18,1003479.224,22316805.9277,22316440.751\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_start_daily_pro_import_accepts_tushare_year_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "daily"
    (source / "2026").mkdir(parents=True)
    _write_tushare_daily_csv(source / "2026" / "000001_SZ.csv")
    store = JobStore(store_dir=tmp_path / "jobs")
    monkeypatch.setattr(daily_pro_import, "job_store", store)
    async def no_background_import(*_args: object) -> None:
        return None

    monkeypatch.setattr(daily_pro_import, "_run_import", no_background_import)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path / "data"))))
    )

    result = await daily_pro_import.start_import(
        daily_pro_import.DailyProImportRequest(source_dir=str(source)), request
    )

    assert result["reused"] is False
    assert store.active_id()
    assert store.get(store.active_id())["timeout_s"] == daily_pro_import.LOCAL_IMPORT_TIMEOUT_S


@pytest.mark.asyncio
async def test_start_daily_pro_import_rejects_empty_or_nonannual_directory(tmp_path: Path) -> None:
    with pytest.raises(HTTPException, match="请输入"):
        await daily_pro_import.start_import(daily_pro_import.DailyProImportRequest(source_dir="  "), None)  # type: ignore[arg-type]
    source = tmp_path / "daily"
    source.mkdir()
    _write_tushare_daily_csv(source / "000001_SZ.csv")
    with pytest.raises(HTTPException, match="年份目录"):
        await daily_pro_import.start_import(
            daily_pro_import.DailyProImportRequest(source_dir=str(source)), None  # type: ignore[arg-type]
        )


def test_daily_pro_import_releases_run_slot_before_marking_job_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "daily"
    (source / "2026").mkdir(parents=True)
    _write_tushare_daily_csv(source / "2026" / "000001_SZ.csv")
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

    monkeypatch.setattr(daily_pro_import, "job_store", store)
    monkeypatch.setattr(daily_pro_import, "try_acquire_run_slot", lambda: True)
    monkeypatch.setattr(daily_pro_import, "release_run_slot", lambda: events.append("release"))
    monkeypatch.setattr(daily_pro_import, "invalidate_data_cache", lambda: events.append("invalidate_cache"))
    monkeypatch.setattr(store, "succeed", succeed_after_release)

    daily_pro_import._import_sync(job_id, source, tmp_path / "data", Repo())

    current_import = max(index for index, event in enumerate(events) if event == "invalidate_cache")
    assert events.index("refresh_cache", current_import) < events.index("release", current_import) < events.index("succeed")
    assert store.get(job_id)["status"] == "succeeded"
