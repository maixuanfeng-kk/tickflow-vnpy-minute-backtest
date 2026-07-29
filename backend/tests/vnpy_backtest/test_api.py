import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import backtest
from app.api import strategy


def test_no_production_native_minute_portfolio_route_or_import_remains() -> None:
    assert "minute_portfolio" not in Path(backtest.__file__).read_text(encoding="utf-8")
    assert "/minute-portfolio/stream" not in Path(backtest.__file__).read_text(encoding="utf-8")
    assert "minute_portfolio" not in Path(strategy.__file__).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_vnpy_stream_emits_progress_then_result(monkeypatch) -> None:
    class Service:
        def __init__(self, repo):
            assert repo == "repo"

        def run(self, config):
            assert config.symbols == ("600000.SH",)
            assert config.strategy_id == "opening_volume_portfolio"
            assert config.position_sizing == "score_weight"
            assert config.max_buy_volume_ratio == 1.0
            assert config.max_sell_volume_ratio == 0.5
            assert config.candidate_sort == "score"
            assert config.force_close_at_end is False
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
        max_buy_volume_ratio=1.0,
        max_sell_volume_ratio=0.5,
        candidate_sort="score",
        force_close_at_end=False,
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    text = "".join(chunks)

    assert "event: progress" in text
    assert "event: done" in text
    assert json.loads(text.split("event: done\ndata: ", 1)[1].split("\n\n", 1)[0])["run_id"] == "run-1"
