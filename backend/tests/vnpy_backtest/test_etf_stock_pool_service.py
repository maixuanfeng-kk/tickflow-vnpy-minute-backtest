from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.vnpy_backtest.service import VnpyMinuteBacktestConfig, VnpyMinuteBacktestService
from app.vnpy_backtest.strategies.registry import get_strategy


def test_stock_pool_service_rejects_days_missing_from_stock_replay(monkeypatch) -> None:
    service = VnpyMinuteBacktestService(SimpleNamespace())
    shadow_result = {
        "equity_curve": [
            {"date": "2026-05-06 15:00:00", "value": 1.0},
            {"date": "2026-05-07 15:00:00", "value": 1.0},
        ],
        "signal_diagnostics": [],
        "stats": {},
    }
    frame = pl.DataFrame({
        "symbol": ["000001.SZ"],
        "datetime": [datetime(2026, 5, 6, 9, 30)],
        "open": [10.0],
        "high": [10.0],
        "low": [10.0],
        "close": [10.0],
        "volume": [100.0],
        "amount": [1_000.0],
    })
    monkeypatch.setattr(service, "_monthly_stock_pools", lambda: {
        "2026-05": {
            "effective_date": "2026-05-06",
            "member_count": 1,
            "symbols": ["000001.SZ"],
        },
    })
    monkeypatch.setattr(service, "_run_portfolio", lambda *args: shadow_result)
    monkeypatch.setattr(service, "_etf_shadow_events", lambda *args: {})
    monkeypatch.setattr(service, "_instrument_metadata", lambda *args: ({}, {}))
    monkeypatch.setattr(service, "_daily_market_metadata", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        service,
        "_tushare_minute_trading_days",
        lambda *args: [date(2026, 5, 6), date(2026, 5, 7)],
    )
    monkeypatch.setattr(
        service,
        "_iter_tushare_minute_days",
        lambda *args: iter([(date(2026, 5, 6), frame)]),
    )
    config = VnpyMinuteBacktestConfig(
        strategy_id="etf_159915_stock_pool",
        start=date(2026, 5, 6),
        end=date(2026, 5, 7),
        initial_capital=10_000_000,
    )

    with pytest.raises(ValueError, match="股票分钟数据缺少 1 个交易日: 2026-05-07"):
        service._run_etf_stock_pool(
            config,
            get_strategy("etf_159915_stock_pool"),
            (),
        )


def test_stock_pool_result_exposes_monthly_snapshot_metadata(monkeypatch) -> None:
    service = VnpyMinuteBacktestService(SimpleNamespace())
    monthly_pools = {
        "2026-05": {
            "effective_date": "2026-05-06",
            "pool_key": "generated:monthly_growth_trend:2026-05",
            "source": "stock_pool",
            "member_count": 1,
            "symbols": ["000001.SZ"],
        },
    }
    shadow_result = {
        "equity_curve": [{"date": "2026-05-06 15:00:00", "value": 1.0}],
        "signal_diagnostics": [],
        "stats": {},
    }
    frame = pl.DataFrame({
        "symbol": ["000001.SZ"],
        "datetime": [datetime(2026, 5, 6, 9, 30)],
        "open": [10.0],
        "high": [10.0],
        "low": [10.0],
        "close": [10.0],
        "volume": [100.0],
        "amount": [1_000.0],
    })
    monkeypatch.setattr(service, "_monthly_stock_pools", lambda: monthly_pools)
    monkeypatch.setattr(service, "_run_portfolio", lambda *args: shadow_result)
    monkeypatch.setattr(service, "_etf_shadow_events", lambda *args: {})
    monkeypatch.setattr(service, "_instrument_metadata", lambda *args: ({}, {}))
    monkeypatch.setattr(service, "_daily_market_metadata", lambda *args, **kwargs: {})
    monkeypatch.setattr(service, "_tushare_minute_trading_days", lambda *args: [date(2026, 5, 6)])
    monkeypatch.setattr(
        service,
        "_iter_tushare_minute_days",
        lambda *args: iter([(date(2026, 5, 6), frame)]),
    )
    monkeypatch.setattr(service, "_assemble_portfolio_result", lambda **kwargs: {
        "stats": {},
        "strategy_info": {},
        "equity_curve": [],
    })
    config = VnpyMinuteBacktestConfig(
        strategy_id="etf_159915_stock_pool",
        start=date(2026, 5, 6),
        end=date(2026, 5, 6),
        initial_capital=10_000_000,
    )

    result = service._run_etf_stock_pool(
        config,
        get_strategy("etf_159915_stock_pool"),
        (),
    )

    assert result["strategy_info"]["monthly_pool_counts"] == {"2026-05": 1}
    assert result["strategy_info"]["monthly_pools"]["2026-05"]["source"] == "stock_pool"
