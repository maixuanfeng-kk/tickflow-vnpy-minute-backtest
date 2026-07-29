import asyncio
import json
from types import SimpleNamespace

import pytest

from app.api import backtest


@pytest.mark.asyncio
async def test_vnpy_stream_emits_progress_then_result(monkeypatch) -> None:
    class Service:
        def __init__(self, repo):
            assert repo == "repo"

        def run(self, config):
            assert config.symbols == ("600000.SH",)
            assert config.strategy_id == "opening_breakout_pool"
            assert config.position_sizing == "score_weight"
            assert config.max_volume_ratio is None
            return {"run_id": "run-1", "stats": {}, "trades": []}

    async def not_disconnected():
        return False

    monkeypatch.setattr("app.vnpy_backtest.service.VnpyMinuteBacktestService", Service)
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
        max_volume_ratio=0,
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    text = "".join(chunks)

    assert "event: progress" in text
    assert "event: done" in text
    assert json.loads(text.split("event: done\ndata: ", 1)[1].split("\n\n", 1)[0])["run_id"] == "run-1"
