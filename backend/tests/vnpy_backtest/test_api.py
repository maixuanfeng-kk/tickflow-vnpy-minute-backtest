import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.api import backtest


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
