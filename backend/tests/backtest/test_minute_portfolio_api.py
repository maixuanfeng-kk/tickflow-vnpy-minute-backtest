from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import backtest


@pytest.mark.asyncio
async def test_minute_portfolio_stream_passes_user_capital_and_positions(monkeypatch) -> None:
    captured = {}

    class Service:
        def __init__(self, repo) -> None:
            assert repo == "repo"

        def run(self, config):
            captured["config"] = config
            return {"stats": {"end_balance": config.initial_capital}}

    async def not_disconnected() -> bool:
        return False

    monkeypatch.setattr("app.services.watchlist.list_symbols", lambda: [{"symbol": "600000.SH"}])
    monkeypatch.setattr("app.backtest.minute_portfolio.MinutePortfolioService", Service)
    backtest._running_jobs.clear()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(repo="repo")),
        is_disconnected=not_disconnected,
    )

    response = await backtest.minute_portfolio_stream(
        request,
        start="2026-01-05",
        end="2026-01-06",
        initial_capital=2_000_000.0,
        max_positions=4,
    )
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else chunk
        async for chunk in response.body_iterator
    ]
    body = "".join(chunks)

    assert captured["config"].initial_capital == 2_000_000.0
    assert captured["config"].max_positions == 4
    assert "event: done" in body


@pytest.mark.asyncio
async def test_minute_portfolio_stream_rejects_non_finite_capital(monkeypatch) -> None:
    monkeypatch.setattr("app.services.watchlist.list_symbols", lambda: [{"symbol": "600000.SH"}])

    with pytest.raises(HTTPException) as exc_info:
        await backtest.minute_portfolio_stream(
            SimpleNamespace(),
            start="2026-01-05",
            end="2026-01-06",
            initial_capital=float("nan"),
        )

    assert exc_info.value.status_code == 400
