"""Research stock-pool construction API."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.stock_pools.registry import list_strategies
from app.stock_pools.service import StockPoolService

router = APIRouter(prefix="/api/stock-pools", tags=["stock-pools"])


class StockPoolBuildRequest(BaseModel):
    strategy_id: str = "monthly_growth_trend"
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    source_pool_key: str | None = None
    params: dict[str, object] = Field(default_factory=dict)


def _service(request: Request) -> StockPoolService:
    return StockPoolService(request.app.state.repo)


@router.get("/strategies")
def strategies() -> dict:
    return {"strategies": [item.to_public_dict() for item in list_strategies()]}


@router.get("/readiness")
def readiness(request: Request, month: str, source_pool_key: str | None = None, strategy_id: str = "monthly_growth_trend") -> dict:
    if not any(item.id == strategy_id for item in list_strategies()):
        raise HTTPException(400, f"不支持的股票池策略: {strategy_id}")
    try:
        return _service(request).readiness(month, source_pool_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/preview")
def preview(request: Request, body: StockPoolBuildRequest) -> dict:
    try:
        return _service(request).build(
            body.strategy_id,
            body.month,
            body.params,
            source_pool_key=body.source_pool_key,
        ).to_dict()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/save")
def save(request: Request, body: StockPoolBuildRequest) -> dict:
    try:
        return _service(request).save(
            body.strategy_id,
            body.month,
            body.params,
            source_pool_key=body.source_pool_key,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/runs")
def runs(request: Request, strategy_id: str | None = None) -> dict:
    return {"runs": _service(request).list_runs(strategy_id)}


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: str) -> dict:
    result = _service(request).get_run(run_id)
    if result is None:
        raise HTTPException(404, "股票池构建记录不存在")
    return result
