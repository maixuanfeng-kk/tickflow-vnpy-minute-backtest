"""vn.py 股票池分钟回测 API。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from datetime import date, time as dt_time
from typing import Literal
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/backtest", tags=["backtest"])
logger = logging.getLogger(__name__)


class _BacktestJob:
    __slots__ = ("cancel_event", "done", "error", "finish_ts", "key", "progress", "result")

    def __init__(self, key: str):
        self.key = key
        self.cancel_event = threading.Event()
        self.progress: list[dict] = []
        self.result = None
        self.error: str | None = None
        self.done = False
        self.finish_ts = 0.0


_running_jobs: dict[str, _BacktestJob] = {}
_jobs_lock = threading.Lock()
_JOB_TTL = 300


def _normalize_symbols(value: str | None) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item.strip().upper()
            for item in (value.split(",") if value else [])
            if item.strip()
        )
    )


def _parse_vnpy_time(value: str | None, default: str) -> dt_time:
    raw = default if value is None else value
    try:
        if len(raw) != 5 or raw[2] != ":":
            raise ValueError
        parsed = dt_time.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="start_time 和 end_time 必须是 HH:MM 时间") from exc
    return parsed


def _parse_vnpy_scope(
    strategy_id: str,
    symbols: str | None,
    start: str,
    end: str,
    start_time: str | None = None,
    end_time: str | None = None,
):
    from app.vnpy_backtest.strategies.registry import get_strategy

    spec = get_strategy(strategy_id)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"不支持的 vn.py 策略: {strategy_id}")
    normalized_symbols = _normalize_symbols(symbols)
    if not spec.min_symbols <= len(normalized_symbols) <= spec.max_symbols:
        raise HTTPException(
            status_code=400,
            detail=f"{spec.name} 支持 {spec.min_symbols}-{spec.max_symbols} 只股票",
        )
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="start 和 end 必须是 ISO 日期") from exc
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end 不能早于 start")
    start_time_value = _parse_vnpy_time(start_time, "09:30")
    end_time_value = _parse_vnpy_time(end_time, "15:00")
    if start_date == end_date and start_time_value > end_time_value:
        raise HTTPException(status_code=400, detail="开始时间不能晚于结束时间")
    return spec, normalized_symbols, start_date, end_date, start_time_value, end_time_value


def _readiness_for_scope(
    request: Request,
    strategy_id: str,
    symbols: tuple[str, ...],
    start: date,
    end: date,
) -> dict:
    if strategy_id != "etf_159915_minute":
        return {
            "strategy_id": strategy_id,
            "ready": True,
            "blocking_reasons": [],
            "warnings": [],
            "coverage": {},
        }
    from app.vnpy_backtest.readiness import Etf159915ReadinessService

    service = Etf159915ReadinessService(request.app.state.repo.store.data_dir)
    return service.check(symbol=symbols[0], start=start, end=end)


def _job_key(
    *,
    strategy_id: str,
    symbols: tuple[str, ...],
    start: str,
    end: str,
    initial_capital: float,
    commission_pct: float,
    stamp_tax_pct: float,
    slippage_bps: float,
    max_positions: int,
    position_sizing: str,
    volume_limit_enabled: bool,
    signal_price_basis: str,
    params: str | None,
    start_time: str = "09:30",
    end_time: str = "15:00",
) -> str:
    raw = (
        f"vnpy|{strategy_id}|{','.join(symbols)}|{start}|{end}|{start_time}|{end_time}|{initial_capital}|"
        f"{commission_pct}|{stamp_tax_pct}|{slippage_bps}|{max_positions}|"
        f"{position_sizing}|{volume_limit_enabled}|{signal_price_basis}|{params}"
    )
    return f"vnpy:{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def _cleanup_stale_jobs() -> None:
    now = time.time()
    with _jobs_lock:
        stale = [
            key
            for key, job in _running_jobs.items()
            if job.done and now - job.finish_ts > _JOB_TTL
        ]
        for key in stale:
            _running_jobs.pop(key, None)


def _finish_job(job: _BacktestJob, *, result=None, error: str | None = None) -> None:
    with _jobs_lock:
        job.result = result
        job.error = error
        job.done = True
        job.finish_ts = time.time()


@router.get("/vnpy/strategies")
def vnpy_strategies() -> dict:
    from app.vnpy_backtest.strategies.registry import list_strategies

    return {"strategies": [strategy.to_public_dict() for strategy in list_strategies()]}


@router.get("/vnpy/readiness")
def vnpy_readiness(
    request: Request,
    strategy_id: str,
    symbols: str,
    start: str,
    end: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> dict:
    spec, normalized_symbols, start_date, end_date, _, _ = _parse_vnpy_scope(
        strategy_id, symbols, start, end, start_time, end_time,
    )
    return _readiness_for_scope(
        request, spec.id, normalized_symbols, start_date, end_date,
    )


@router.get("/vnpy/stream")
async def vnpy_stream(
    request: Request,
    start: str,
    end: str,
    symbols: str | None = None,
    strategy_id: str = "opening_breakout_pool",
    initial_capital: float = 100_000.0,
    commission_pct: float = 0.0002,
    stamp_tax_pct: float = 0.001,
    slippage_bps: float = 5.0,
    max_positions: int = 10,
    position_sizing: Literal["equal", "score_weight"] = "equal",
    volume_limit_enabled: bool = True,
    signal_price_basis: Literal["qfq", "raw"] = "qfq",
    params: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
):
    """使用本地分钟 K 数据运行 vn.py 股票池组合回测。"""
    spec, normalized_symbols, start_date, end_date, start_time_value, end_time_value = _parse_vnpy_scope(
        strategy_id, symbols, start, end, start_time, end_time,
    )
    readiness = _readiness_for_scope(
        request, spec.id, normalized_symbols, start_date, end_date,
    )
    if not readiness["ready"]:
        raise HTTPException(
            status_code=400,
            detail="；".join(readiness["blocking_reasons"]),
        )
    if initial_capital <= 0:
        raise HTTPException(status_code=400, detail="initial_capital 必须大于 0")
    if max_positions < 1:
        raise HTTPException(status_code=400, detail="max_positions 必须至少为 1")
    if min(commission_pct, stamp_tax_pct, slippage_bps) < 0:
        raise HTTPException(status_code=400, detail="费率和滑点不能为负数")
    try:
        strategy_params = json.loads(params) if params else {}
        if not isinstance(strategy_params, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="params 必须是 JSON 对象") from exc

    job_key = _job_key(
        strategy_id=strategy_id,
        symbols=normalized_symbols,
        start=start,
        end=end,
        start_time=start_time_value.isoformat(timespec="minutes"),
        end_time=end_time_value.isoformat(timespec="minutes"),
        initial_capital=initial_capital,
        commission_pct=commission_pct,
        stamp_tax_pct=stamp_tax_pct,
        slippage_bps=slippage_bps,
        max_positions=max_positions,
        position_sizing=position_sizing,
        volume_limit_enabled=volume_limit_enabled,
        signal_price_basis=signal_price_basis,
        params=params,
    )
    _cleanup_stale_jobs()
    with _jobs_lock:
        job = _running_jobs.get(job_key)
        if job is None:
            job = _BacktestJob(job_key)
            _running_jobs[job_key] = job
            is_new = True
        else:
            is_new = False

    if is_new:
        def run_vnpy() -> None:
            try:
                job.progress.append(
                    {"day": 0, "total": 1, "date": "加载分钟K", "equity": initial_capital}
                )
                try:
                    from app.vnpy_backtest.service import (
                        VnpyMinuteBacktestConfig,
                        VnpyMinuteBacktestService,
                    )
                except ModuleNotFoundError as exc:
                    if exc.name in {"vnpy", "vnpy_ctastrategy"} or (
                        exc.name and exc.name.startswith("vnpy")
                    ):
                        raise RuntimeError(
                            "vn.py 股票池分钟回测需要安装可选依赖: "
                            "uv sync --extra vnpy-backtest"
                        ) from exc
                    raise

                config = VnpyMinuteBacktestConfig(
                    symbols=normalized_symbols,
                    strategy_id=strategy_id,
                    start=start_date,
                    end=end_date,
                    start_time=start_time_value,
                    end_time=end_time_value,
                    initial_capital=initial_capital,
                    commission_pct=commission_pct,
                    stamp_tax_pct=stamp_tax_pct,
                    slippage_bps=slippage_bps,
                    max_volume_ratio=0.10 if volume_limit_enabled else None,
                    max_positions=max_positions,
                    position_sizing=position_sizing,
                    signal_price_basis=signal_price_basis,
                    params=strategy_params,
                    is_cancelled=job.cancel_event.is_set,
                    on_progress=lambda day, total, trading_day, equity: job.progress.append(
                        {
                            "day": day,
                            "total": total,
                            "date": trading_day.isoformat(),
                            "equity": equity,
                        }
                    ),
                )
                result = VnpyMinuteBacktestService(request.app.state.repo).run(config)
                job.progress.append(
                    {
                        "day": 1,
                        "total": 1,
                        "date": "完成",
                        "equity": result.get("stats", {}).get("end_balance", initial_capital),
                    }
                )
                _finish_job(job, result=result)
            except Exception as exc:
                logger.exception("vn.py portfolio minute backtest failed")
                _finish_job(job, error=str(exc))

        threading.Thread(target=run_vnpy, daemon=True).start()

    async def event_generator():
        cursor = 0
        while True:
            while cursor < len(job.progress):
                yield (
                    "event: progress\n"
                    f"data: {json.dumps(job.progress[cursor], ensure_ascii=False)}\n\n"
                )
                cursor += 1
            if job.done:
                event = "error" if job.error else "done"
                payload = {"message": job.error} if job.error else job.result
                yield f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.05)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/strategy/cancel")
async def strategy_cancel(request: Request) -> dict:
    body = await request.json()
    parsed = parse_qs(body.get("qs", ""))

    def get(key: str, default: str = "") -> str:
        return parsed.get(key, [default])[0]

    try:
        volume_limit_enabled = get("volume_limit_enabled", "true").lower() not in {
            "false", "0", "no", "off",
        }
        job_key = _job_key(
            strategy_id=get("strategy_id", "opening_breakout_pool"),
            symbols=_normalize_symbols(get("symbols")),
            start=get("start"),
            end=get("end"),
            start_time=get("start_time", "09:30"),
            end_time=get("end_time", "15:00"),
            initial_capital=float(get("initial_capital", "100000")),
            commission_pct=float(get("commission_pct", "0.0002")),
            stamp_tax_pct=float(get("stamp_tax_pct", "0.001")),
            slippage_bps=float(get("slippage_bps", "5")),
            max_positions=int(get("max_positions", "10")),
            position_sizing=get("position_sizing", "equal"),
            volume_limit_enabled=volume_limit_enabled,
            signal_price_basis=get("signal_price_basis", "qfq"),
            params=get("params") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="取消参数格式错误") from exc
    job = _running_jobs.get(job_key)
    if job and not job.done:
        job.cancel_event.set()
        return {"ok": True}
    return {"ok": False, "message": "任务不存在或已完成"}
