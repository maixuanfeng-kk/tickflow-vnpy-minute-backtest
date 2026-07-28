from inspect import signature
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import backtest


def test_minute_portfolio_stream_accepts_range_and_advanced_overrides() -> None:
    parameters = signature(backtest.minute_portfolio_stream).parameters

    assert "symbols" in parameters
    assert "overrides" in parameters
    assert "max_exposure_pct" in parameters
    assert {"candidate_sort", "entry_fill", "exit_fill", "force_close_at_end"} <= set(parameters)


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
    assert captured["config"].candidate_sort == "volume_ratio"
    assert captured["config"].entry_fill == "next_minute_open"
    assert captured["config"].exit_fill == "next_minute_open"
    assert captured["config"].force_close_at_end is True
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
        params=(
            '{"branch_a_volume_multiple": 2.0, "enable_branch_a": false, '
            '"branch_c_previous_candle": "阴线"}'
        ),
    )
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else chunk
        async for chunk in response.body_iterator
    ]

    assert captured["config"].strategy_params.branch_a_volume_multiple == 2.0
    assert captured["config"].strategy_params.enable_branch_a is False
    assert captured["config"].strategy_params.branch_c_previous_candle == "bearish"
    assert "event: done" in "".join(chunks)


@pytest.mark.asyncio
async def test_minute_portfolio_stream_builds_advanced_config_and_watchlist_subset(monkeypatch, tmp_path) -> None:
    captured = {}

    class Service:
        def __init__(self, repo) -> None:
            pass

        def run(self, config, progress_callback=None):
            captured["config"] = config
            return {"stats": {"end_balance": config.initial_capital}}

    monkeypatch.setattr("app.services.watchlist.list_symbols", lambda: [
        {"symbol": "600000.SH"},
        {"symbol": "000001.SZ"},
        {"symbol": "300001.SZ"},
    ])
    monkeypatch.setattr("app.backtest.minute_portfolio.MinutePortfolioService", Service)
    backtest._running_jobs.clear()
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            strategy_engine=SimpleNamespace(get=lambda strategy_id: SimpleNamespace(
                execution_backend="minute_native",
                meta={"id": strategy_id, "scoring": {"volume_ratio": 1.0, "today_return": 0.0}},
                basic_filter={"enabled": False, "price_max": 300.0},
            )),
        )),
        is_disconnected=lambda: False,
    )

    response = await backtest.minute_portfolio_stream(
        request,
        start="2026-01-05",
        end="2026-01-06",
        symbols="000001.SZ,600000.SH,999999.SH",
        max_exposure_pct=0.97,
        candidate_sort="score",
        entry_fill="signal_minute_close",
        exit_fill="next_minute_open",
        force_close_at_end=False,
        params='{"stop_loss_pct": 0.025}',
        overrides=(
            '{"basic_filter":{"enabled":true,"price_min":5,"amount_min":100000000,'
            '"boards":["沪主板"],"exclude_st":true},'
            '"scoring":{"volume_ratio":0.7,"today_return":0.3},'
            '"score_min":60,"score_max":90,"take_profit":0.1,'
            '"trailing_stop":-0.03,"trailing_take_profit_activate":0.08,'
            '"trailing_take_profit_drawdown":0.02,"max_hold_days":7}'
        ),
    )
    chunks = [
        chunk.decode() if isinstance(chunk, bytes) else chunk
        async for chunk in response.body_iterator
    ]

    config = captured["config"]
    assert config.symbols == ["600000.SH", "000001.SZ"]
    assert config.strategy_params.stop_loss_pct == 0.025
    assert config.cash_reserve_ratio == pytest.approx(0.03)
    assert config.candidate_sort == "score"
    assert config.entry_fill == "signal_minute_close"
    assert config.exit_fill == "next_minute_open"
    assert config.force_close_at_end is False
    assert config.basic_filter["price_min"] == 5
    assert config.basic_filter["price_max"] == 300.0
    assert config.basic_filter["amount_min"] == 100_000_000
    assert config.scoring == {"volume_ratio": 0.7, "today_return": 0.3}
    assert config.score_min == 60
    assert config.score_max == 90
    assert config.take_profit_pct == 0.1
    assert config.trailing_stop_pct == 0.03
    assert config.trailing_take_profit_activate_pct == 0.08
    assert config.trailing_take_profit_drawdown_pct == 0.02
    assert config.max_hold_days == 7
    assert "event: done" in "".join(chunks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"candidate_sort": "unknown"},
        {"entry_fill": "close_t"},
        {"exit_fill": "open_t+1"},
    ],
)
async def test_minute_portfolio_stream_rejects_invalid_execution_controls(
    monkeypatch,
    tmp_path,
    values: dict,
) -> None:
    monkeypatch.setattr(
        "app.services.watchlist.list_symbols",
        lambda: [{"symbol": "600000.SH"}],
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            strategy_engine=None,
        )),
    )

    with pytest.raises(HTTPException) as exc_info:
        await backtest.minute_portfolio_stream(
            request,
            start="2026-01-05",
            end="2026-01-06",
            **values,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        '{"take_profit":"invalid"}',
        '{"scoring":{"volume_ratio":null}}',
        '{"scoring":"invalid"}',
        '{"scoring":[]}',
        '{"max_hold_days":"invalid"}',
        '{"max_hold_days":1.5}',
        '{"max_hold_days":1e400}',
    ],
)
async def test_minute_portfolio_stream_rejects_invalid_advanced_numbers(
    monkeypatch,
    tmp_path,
    overrides: str,
) -> None:
    monkeypatch.setattr(
        "app.services.watchlist.list_symbols",
        lambda: [{"symbol": "600000.SH"}],
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            strategy_engine=SimpleNamespace(get=lambda strategy_id: SimpleNamespace(
                execution_backend="minute_native",
                meta={"id": strategy_id, "scoring": {}},
                basic_filter={"enabled": False},
            )),
        )),
        is_disconnected=lambda: False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await backtest.minute_portfolio_stream(
            request,
            start="2026-01-05",
            end="2026-01-06",
            overrides=overrides,
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saved_override",
    [
        {"basic_filter": "invalid"},
        {"scoring": []},
    ],
)
async def test_minute_portfolio_stream_rejects_invalid_saved_nested_overrides(
    monkeypatch,
    tmp_path,
    saved_override: dict,
) -> None:
    monkeypatch.setattr(
        "app.services.watchlist.list_symbols",
        lambda: [{"symbol": "600000.SH"}],
    )
    monkeypatch.setattr(
        "app.strategy.config.load_override",
        lambda data_dir, strategy_id: saved_override,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            strategy_engine=SimpleNamespace(get=lambda strategy_id: SimpleNamespace(
                execution_backend="minute_native",
                meta={"id": strategy_id, "scoring": {}},
                basic_filter={"enabled": False},
            )),
        )),
        is_disconnected=lambda: False,
    )

    with pytest.raises(HTTPException) as exc_info:
        await backtest.minute_portfolio_stream(
            request,
            start="2026-01-05",
            end="2026-01-06",
        )

    assert exc_info.value.status_code == 400
