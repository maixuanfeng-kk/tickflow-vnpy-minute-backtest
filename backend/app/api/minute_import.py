"""Local minute CSV import API."""
from __future__ import annotations

import asyncio
import concurrent.futures
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.data import _local_minute_import_status, invalidate_data_cache
from app.services.local_minute_import import LocalMinuteCsvImporter, refresh_minute_view
from app.services.pipeline_jobs import (
    LONG_JOB_TIMEOUT_S,
    job_store,
    release_run_slot,
    try_acquire_run_slot,
)

router = APIRouter(prefix="/api/data/minute-import", tags=["minute-import"])
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="minute-import")
_background_tasks: set[asyncio.Task[Any]] = set()


class MinuteImportRequest(BaseModel):
    source_dir: str


def _import_sync(job_id: str, source_dir: Path, data_dir: Path, repo: Any) -> None:
    if not try_acquire_run_slot():
        job_store.fail(job_id, "已有数据任务正在运行, 请稍后再试")
        return
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        job_store.start(job_id)

        def progress(current: int, total: int, stage: str) -> None:
            pct = round(current * 100 / total) if total else 0
            job_store.progress(job_id, "minute_import", pct, f"{stage} ({current}/{total})", stage_pct=pct, skip_log=True)

        summary = LocalMinuteCsvImporter(source_dir, data_dir, progress=progress).run()
        refresh_minute_view(repo)
        invalidate_data_cache()
        repo.refresh_cache()
        result = {"type": "minute_import", "summary": summary.to_dict()}
    except Exception as exc:
        error = str(exc)
    finally:
        release_run_slot()

    if error:
        job_store.fail(job_id, error)
    else:
        job_store.succeed(job_id, result)


async def _run_import(job_id: str, source_dir: Path, data_dir: Path, repo: Any) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_executor, _import_sync, job_id, source_dir, data_dir, repo)


@router.post("")
async def start_import(payload: MinuteImportRequest, request: Request) -> dict[str, Any]:
    source_dir = Path(payload.source_dir.strip()).expanduser()
    if not payload.source_dir.strip():
        raise HTTPException(status_code=400, detail="请输入本地分钟 K CSV 目录")
    if not source_dir.is_dir():
        raise HTTPException(status_code=400, detail="分钟 K CSV 目录不存在")
    if not any(source_dir.glob("*.csv")):
        raise HTTPException(status_code=400, detail="目录中没有 CSV 分钟 K 文件")
    if job_store.active_id():
        raise HTTPException(status_code=409, detail="已有数据任务正在运行, 请稍后再试")

    job_id, is_new = job_store.create(timeout_s=LONG_JOB_TIMEOUT_S)
    if not is_new:
        raise HTTPException(status_code=409, detail="已有数据任务正在运行, 请稍后再试")
    job_store.progress(job_id, "minute_import", 0, "等待开始", stage_pct=0)
    repo = request.app.state.repo
    task = asyncio.create_task(_run_import(job_id, source_dir, repo.store.data_dir, repo))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"job_id": job_id, "reused": False}


@router.get("/status")
def import_status(request: Request) -> dict[str, Any]:
    active_job = None
    active_id = job_store.active_id()
    if active_id:
        candidate = job_store.get(active_id)
        if candidate and candidate.get("stage") == "minute_import":
            active_job = candidate
    if active_job is None:
        for summary in job_store.list_recent(limit=20):
            if summary.get("stage") == "minute_import":
                active_job = job_store.get(summary["id"]) or summary
                break
    return {
        "manifest": _local_minute_import_status(request.app.state.repo.store.data_dir),
        "job": active_job,
    }
