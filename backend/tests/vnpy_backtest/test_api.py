import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import backtest


def test_vnpy_router_exposes_only_portfolio_backtest_endpoints() -> None:
    routes = {(route.path, frozenset(route.methods or set())) for route in backtest.router.routes}

    assert routes == {
        ("/api/backtest/vnpy/strategies", frozenset({"GET"})),
        ("/api/backtest/vnpy/stream", frozenset({"GET"})),
        ("/api/backtest/strategy/cancel", frozenset({"POST"})),
    }


def test_vnpy_strategy_catalog_contains_four_portfolio_strategies() -> None:
    response = backtest.vnpy_strategies()

    assert {item["id"] for item in response["strategies"]} == {
        "etf_159915_minute",
        "opening_breakout_pool",
        "opening_breakout_condition_1",
        "opening_breakout_condition_2",
        "opening_breakout_condition_3",
    }
    assert all(item["kind"] == "portfolio" for item in response["strategies"])


@pytest.mark.asyncio
async def test_vnpy_stream_emits_progress_then_result(monkeypatch) -> None:
    class Config:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Service:
        def __init__(self, repo):
            assert repo == "repo"

        def run(self, config):
            assert config.symbols == ("600000.SH",)
            assert config.position_sizing == "score_weight"
            assert config.max_volume_ratio is None
            return {"run_id": "run-1", "stats": {}, "trades": []}

    fake_service = ModuleType("app.vnpy_backtest.service")
    fake_service.VnpyMinuteBacktestConfig = Config
    fake_service.VnpyMinuteBacktestService = Service
    monkeypatch.setitem(sys.modules, "app.vnpy_backtest.service", fake_service)

    async def not_disconnected():
        return False

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo="repo")),
        is_disconnected=not_disconnected,
    )

    response = await backtest.vnpy_stream(
        request,
        symbols="600000.SH",
        start="2026-01-05",
        end="2026-01-05",
        position_sizing="score_weight",
        volume_limit_enabled=False,
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    text = "".join(chunks)

    assert "event: progress" in text
    assert "event: done" in text
    payload = text.split("event: done\ndata: ", 1)[1].split("\n\n", 1)[0]
    assert json.loads(payload)["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_vnpy_stream_rejects_empty_stock_pool() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo="repo")))

    with pytest.raises(HTTPException, match="支持 1-1000 只股票"):
        await backtest.vnpy_stream(
            request,
            symbols="",
            start="2026-01-05",
            end="2026-01-05",
        )


@pytest.mark.asyncio
async def test_vnpy_cancel_uses_same_job_key() -> None:
    symbols = ("600000.SH",)
    job_key = backtest._job_key(
        strategy_id="opening_breakout_pool",
        symbols=symbols,
        start="2026-01-05",
        end="2026-01-06",
        initial_capital=100_000.0,
        commission_pct=0.0002,
        stamp_tax_pct=0.001,
        slippage_bps=5.0,
        max_positions=10,
        position_sizing="equal",
        volume_limit_enabled=True,
        signal_price_basis="qfq",
        params=None,
    )
    job = backtest._BacktestJob(job_key)
    backtest._running_jobs[job_key] = job

    async def json_body():
        return {
            "qs": (
                "engine=vnpy&strategy_id=opening_breakout_pool&symbols=600000.SH&"
                "start=2026-01-05&end=2026-01-06&initial_capital=100000&"
                "commission_pct=0.0002&stamp_tax_pct=0.001&slippage_bps=5&"
                "max_positions=10&position_sizing=equal&volume_limit_enabled=true&"
                "signal_price_basis=qfq"
            )
        }

    try:
        response = await backtest.strategy_cancel(SimpleNamespace(json=json_body))
        assert response == {"ok": True}
        assert job.cancel_event.is_set()
    finally:
        backtest._running_jobs.pop(job_key, None)
