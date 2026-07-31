from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import minute_import
from app.services.pipeline_jobs import JobStore


def _write_minute_csv(path: Path) -> None:
    path.write_text(
        "generated fixture\n"
        "股票代码,k线结束时间,开盘价,收盘价,最高价,最低价,成交量,成交额\n"
        "sh600000,2026-01-05 09:30:00,10,10.1,10.2,9.9,100,100000\n",
        encoding="gbk",
    )


@pytest.mark.asyncio
async def test_start_minute_import_accepts_local_directory_and_hides_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "minute-source"
    source.mkdir()
    _write_minute_csv(source / "sh600000.csv")
    store = JobStore(store_dir=tmp_path / "jobs")
    monkeypatch.setattr(minute_import, "job_store", store)

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path / "data"))))
    )
    result = await minute_import.start_import(
        minute_import.MinuteImportRequest(source_dir=str(source)), request
    )

    assert result["reused"] is False
    await asyncio.sleep(0)
    status = minute_import.import_status(request)
    assert status["job"]["stage"] == "minute_import"
    assert str(source) not in str(status)


@pytest.mark.asyncio
async def test_start_minute_import_rejects_empty_or_invalid_directory() -> None:
    with pytest.raises(HTTPException, match="请输入"):
        await minute_import.start_import(minute_import.MinuteImportRequest(source_dir="  "), None)  # type: ignore[arg-type]
    with pytest.raises(HTTPException, match="目录不存在"):
        await minute_import.start_import(
            minute_import.MinuteImportRequest(source_dir="D:/missing-minute-source"), None  # type: ignore[arg-type]
        )
