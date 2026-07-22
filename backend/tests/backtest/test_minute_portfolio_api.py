from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import backtest


@pytest.mark.asyncio
async def test_minute_portfolio_stream_passes_user_capital_and_positions(monkeypatch, tmp_path) -> None:
    captured = {}

    class Service:
        def __init__(self, repo) -> None:
            assert repo == "repo"

        def run(self, config, progress_callback=None):
            captured["config"] = config
            if progress_callback:
                progress_callback({"day": 325, "total": 1000, "date": "读取分钟数据 1/2", "equity": config.initial_capital})
                progress_callback({"day": 800, "total": 1000, "date": "撮合交易日 1/2", "equity": config.initial_capital})
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
        minute_data_dir=str(tmp_path),
    )
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else chunk
        async for chunk in response.body_iterator
    ]
    body = "".join(chunks)

    assert captured["config"].initial_capital == 2_000_000.0
    assert captured["config"].max_positions == 4
    assert captured["config"].minute_data_dir == str(tmp_path)
    assert "读取分钟数据 1/2" in body
    assert "撮合交易日 1/2" in body
    assert body.index("读取分钟数据 1/2") < body.index("event: done")
    assert "event: done" in body
    assert "event: done\ndata:" in body


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


@pytest.mark.asyncio
async def test_minute_portfolio_stream_passes_opening_volume_strategy_params(monkeypatch, tmp_path) -> None:
    captured = {}

    class Service:
        def __init__(self, repo) -> None:
            assert repo.store.data_dir == tmp_path

        def run(self, config, progress_callback=None):
            captured["config"] = config
            return {"stats": {"end_balance": config.initial_capital}}

    monkeypatch.setattr("app.services.watchlist.list_symbols", lambda: [{"symbol": "600000.SH"}])
    monkeypatch.setattr("app.backtest.minute_portfolio.MinutePortfolioService", Service)
    backtest._running_jobs.clear()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            strategy_engine=SimpleNamespace(get=lambda strategy_id: SimpleNamespace(
                execution_backend="minute_native",
                meta={"id": strategy_id},
            )),
        )),
        is_disconnected=lambda: False,
    )

    response = await backtest.minute_portfolio_stream(
        request,
        start="2026-01-05",
        end="2026-01-06",
        strategy_id="opening_volume_portfolio",
        params='{"volume_multiple": 2.0, "enable_branch_a": false}',
    )
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else chunk
        async for chunk in response.body_iterator
    ]

    assert captured["config"].strategy_params.volume_multiple == 2.0
    assert captured["config"].strategy_params.enable_branch_a is False
    assert "event: done" in "".join(chunks)
