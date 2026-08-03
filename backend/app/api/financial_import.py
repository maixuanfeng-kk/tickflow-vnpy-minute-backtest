"""本地财务数据导入 API。"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.data import invalidate_data_cache
from app.services.local_financial_import import LocalFinancialCsvImporter
from app.services.pipeline_jobs import (
    LOCAL_IMPORT_TIMEOUT_S,
    job_store,
    release_run_slot,
    try_acquire_run_slot,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/data/financial-import", tags=["financial-import"])
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="financial-import")
_background_tasks: set[asyncio.Task[Any]] = set()


class FinancialImportRequest(BaseModel):
    source_dir: str


def _read_manifest(data_dir: Path) -> dict[str, Any] | None:
    path = data_dir / "user_data" / "local_financial_import.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("读取本地财务导入清单失败: %s", exc)
        return None
    allowed = {
        "status", "dry_run", "source_label", "files_discovered", "files_imported",
        "rows_read", "raw_batches", "income_rows", "skipped_files", "failed_files",
        "imported_at",
    }
    return {key: value for key, value in raw.items() if key in allowed}


def _read_financial_import_status(data_dir: Path, active_job: dict[str, Any] | None) -> dict[str, Any]:
    """汇总已发布财务库, 不返回用户服务器的绝对源目录。"""
    manifest = _read_manifest(data_dir)
    dataset: dict[str, Any] | None = None
    income_path = data_dir / "financials" / "income" / "part.parquet"
    if income_path.exists():
        try:
            income = pl.read_parquet(income_path, columns=["symbol", "period_end", "announce_date"])
            if not income.is_empty():
                dataset = {
                    "rows": income.height,
                    "symbols": income["symbol"].n_unique(),
                    "latest_report_date": str(income["period_end"].max()),
                    "latest_publish_date": str(income["announce_date"].max()),
                }
        except Exception as exc:
            logger.warning("读取本地财务数据摘要失败: %s", exc)
    return {"manifest": manifest, "dataset": dataset, "job": active_job}


def _import_sync(job_id: str, source_dir: Path, data_dir: Path, repo: Any) -> None:
    """在线程池执行导入, 保证重任务锁在整个写入周期内保持。"""
    if not try_acquire_run_slot():
        job_store.fail(job_id, "已有数据任务正在运行, 请稍后再试")
        return
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        job_store.start(job_id)

        def progress(current: int, total: int, stage: str) -> None:
            pct = round(current * 100 / total) if total else 0
            job_store.progress(
                job_id,
                "financial_import",
                pct,
                f"{stage} ({current}/{total})",
                stage_pct=pct,
                skip_log=True,
            )

        summary = LocalFinancialCsvImporter(source_dir, data_dir, progress=progress).run()
        invalidate_data_cache()
        repo.refresh_cache()
        result = {"type": "financial_import", "summary": summary.to_dict()}
    except Exception as exc:
        logger.exception("本地财务数据导入失败")
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
async def start_import(payload: FinancialImportRequest, request: Request) -> dict[str, Any]:
    source_dir = Path(payload.source_dir.strip()).expanduser()
    if not payload.source_dir.strip():
        raise HTTPException(status_code=400, detail="请输入已解压的财务数据目录")
    if not source_dir.is_dir():
        raise HTTPException(status_code=400, detail="财务数据目录不存在")
    if not any(source_dir.rglob("*.csv")):
        raise HTTPException(status_code=400, detail="目录中没有 CSV 财务文件")
    if job_store.active_id():
        raise HTTPException(status_code=409, detail="已有数据任务正在运行, 请稍后再试")

    job_id, is_new = job_store.create(timeout_s=LOCAL_IMPORT_TIMEOUT_S)
    if not is_new:
        raise HTTPException(status_code=409, detail="已有数据任务正在运行, 请稍后再试")
    job_store.progress(job_id, "financial_import", 0, "等待开始", stage_pct=0)
    repo = request.app.state.repo
    task = asyncio.create_task(_run_import(job_id, source_dir, repo.store.data_dir, repo))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"job_id": job_id, "reused": False}


@router.get("/status")
def import_status(request: Request) -> dict[str, Any]:
    data_dir = request.app.state.repo.store.data_dir
    active_job = None
    active_id = job_store.active_id()
    if active_id:
        candidate = job_store.get(active_id)
        if candidate and candidate.get("stage") == "financial_import":
            active_job = candidate
    if active_job is None:
        # 任务结束后保留最近一次导入结果, 前端才能展示失败原因和完成状态。
        for summary in job_store.list_recent(limit=20):
            if summary.get("stage") == "financial_import":
                active_job = job_store.get(summary["id"]) or summary
                break
    return _read_financial_import_status(data_dir, active_job)
