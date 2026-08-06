"""Local Tushare-style daily CSV import API."""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.data import invalidate_data_cache
from app.services.local_daily_pro_import import LocalDailyProCsvImporter
from app.services.pipeline_jobs import (
    LOCAL_IMPORT_TIMEOUT_S,
    job_store,
    release_run_slot,
    try_acquire_run_slot,
)
from app.services.tushare_import import TushareLocalImporter

router = APIRouter(prefix="/api/data/daily-pro-import", tags=["daily-pro-import"])
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="daily-pro-import")
_background_tasks: set[asyncio.Task[Any]] = set()


class DailyProImportRequest(BaseModel):
    source_dir: str


def _manifest(data_dir: Path) -> dict[str, Any] | None:
    path = data_dir / "user_data" / "local_daily_pro_import.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _import_sync(
    job_id: str, source_dir: Path, data_dir: Path, repo: Any, years: list[int] | None = None
) -> None:
    if not try_acquire_run_slot():
        job_store.fail(job_id, "已有数据任务正在运行, 请稍后再试")
        return
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        job_store.start(job_id)

        def progress(current: int, total: int, stage: str) -> None:
            pct = round(current * 100 / total) if total else 0
            job_store.progress(job_id, "daily_pro_import", pct, f"{stage} ({current}/{total})", stage_pct=pct, skip_log=True)

        summary = LocalDailyProCsvImporter(source_dir, data_dir, years=years, progress=progress).run()
        job_store.progress(job_id, "daily_pro_import", 100, "构建 Tushare 复权因子", stage_pct=100, skip_log=True)
        TushareLocalImporter(source_dir, data_dir).build_adjustment_factors()
        invalidate_data_cache()
        repo.refresh_cache()
        result = {"type": "daily_pro_import", "summary": summary.to_dict()}
    except Exception as exc:
        error = str(exc)
    finally:
        release_run_slot()
    if error:
        job_store.fail(job_id, error)
    else:
        job_store.succeed(job_id, result)


async def _run_import(
    job_id: str, source_dir: Path, data_dir: Path, repo: Any, years: list[int] | None = None
) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_executor, _import_sync, job_id, source_dir, data_dir, repo, years)


def _resolve_source(source_dir: Path) -> tuple[Path, list[int] | None]:
    if re.fullmatch(r"\d{4}", source_dir.name) and any(source_dir.glob("*.csv")):
        return source_dir.parent, [int(source_dir.name)]
    years = [path for path in source_dir.iterdir() if path.is_dir() and re.fullmatch(r"\d{4}", path.name)]
    if not years:
        raise HTTPException(status_code=400, detail="目录中没有 YYYY 年份目录")
    if not any(year.glob("*.csv") for year in years):
        raise HTTPException(status_code=400, detail="年份目录中没有 CSV 日K文件")
    return source_dir, None


@router.post("")
async def start_import(payload: DailyProImportRequest, request: Request) -> dict[str, Any]:
    source_dir = Path(payload.source_dir.strip()).expanduser()
    if not payload.source_dir.strip():
        raise HTTPException(status_code=400, detail="请输入本地专业日K CSV 目录")
    if not source_dir.is_dir():
        raise HTTPException(status_code=400, detail="专业日K CSV 目录不存在")
    source_dir, years = _resolve_source(source_dir)
    if job_store.active_id():
        raise HTTPException(status_code=409, detail="已有数据任务正在运行, 请稍后再试")

    job_id, is_new = job_store.create(timeout_s=LOCAL_IMPORT_TIMEOUT_S)
    if not is_new:
        raise HTTPException(status_code=409, detail="已有数据任务正在运行, 请稍后再试")
    job_store.progress(job_id, "daily_pro_import", 0, "等待开始", stage_pct=0)
    repo = request.app.state.repo
    task = asyncio.create_task(_run_import(job_id, source_dir, repo.store.data_dir, repo, years))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"job_id": job_id, "reused": False}


@router.get("/status")
def import_status(request: Request) -> dict[str, Any]:
    active_job = None
    active_id = job_store.active_id()
    if active_id:
        candidate = job_store.get(active_id)
        if candidate and candidate.get("stage") == "daily_pro_import":
            active_job = candidate
    if active_job is None:
        for summary in job_store.list_recent(limit=20):
            if summary.get("stage") == "daily_pro_import":
                active_job = job_store.get(summary["id"]) or summary
                break
    return {"manifest": _manifest(request.app.state.repo.store.data_dir), "job": active_job}
