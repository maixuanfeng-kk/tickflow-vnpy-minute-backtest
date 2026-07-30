from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.vnpy_backtest.service import VnpyMinuteBacktestConfig, VnpyMinuteBacktestService
from app.vnpy_backtest.strategies.registry import list_strategies


class _Repo:
    def iter_minute_days(self, symbols, start, end):
        assert symbols == ["600000.SH"]
        base = datetime(2026, 1, 5, 9, 30)
        yield date(2026, 1, 5), pl.DataFrame(
            {
                "symbol": ["600000.SH"] * 22,
                "datetime": [base + timedelta(minutes=i) for i in range(22)],
                "open": [10.0] * 20 + [12.0, 12.0],
                "high": [10.0] * 20 + [12.0, 12.0],
                "low": [10.0] * 20 + [12.0, 12.0],
                "close": [10.0] * 20 + [12.0, 12.0],
                "volume": [100_000.0] * 22,
                "amount": [1_000_000.0] * 22,
            }
        )

    def minute_trading_days(self, start, end):
        return [date(2026, 1, 5)]

    def get_instruments(self):
        return pl.DataFrame()


def test_vnpy_service_replays_portfolio_minute_bars() -> None:
    result = VnpyMinuteBacktestService(_Repo()).run(
        VnpyMinuteBacktestConfig(
            symbols=("600000.SH",),
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            params={},
        )
    )

    assert result["config"]["engine"] == "vnpy"
    assert result["config"]["frequency"] == "1m"
    assert result["strategy_info"]["id"] == "opening_volume_portfolio"
    assert result["stats"]["symbols_requested"] == 1


def test_registry_exposes_opening_volume_portfolio_only() -> None:
    assert [item.id for item in list_strategies()] == ["opening_volume_portfolio"]


def test_vnpy_service_keeps_native_style_execution_options() -> None:
    result = VnpyMinuteBacktestService(_Repo()).run(
        VnpyMinuteBacktestConfig(
            symbols=("600000.SH",),
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            max_buy_volume_ratio=1.0,
            max_sell_volume_ratio=0.5,
            force_close_at_end=True,
            candidate_sort="volume_ratio",
            entry_fill="next_minute_open",
            exit_fill="next_minute_open",
        )
    )

    assert result["config"]["max_buy_volume_ratio"] == 1.0
    assert result["config"]["max_sell_volume_ratio"] == 0.5
    assert result["config"]["force_close_at_end"] is True
    assert result["config"]["candidate_sort"] == "volume_ratio"


def test_vnpy_service_returns_visible_opening_volume_risk_settings() -> None:
    result = VnpyMinuteBacktestService(_Repo()).run(
        VnpyMinuteBacktestConfig(
            symbols=("600000.SH",),
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            params={
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.05,
                "trailing_stop_pct": 0.03,
                "trailing_take_profit_activate_pct": 0.08,
                "trailing_take_profit_drawdown_pct": 0.02,
                "max_hold_days": 3,
            },
        )
    )

    assert result["strategy_info"] == {
        "id": "opening_volume_portfolio",
        "name": "开盘突破股票池（vn.py）",
        "source": "vnpy",
        "stop_loss": 0.02,
        "take_profit": 0.05,
        "trailing_stop": 0.03,
        "trailing_take_profit_activate": 0.08,
        "trailing_take_profit_drawdown": 0.02,
        "max_hold_days": 3,
    }


def test_vnpy_service_rejects_empty_repository_data() -> None:
    class EmptyRepo:
        def iter_minute_days(self, *args, **kwargs):
            return iter(())

        def minute_trading_days(self, *args, **kwargs):
            return []

        def get_instruments(self):
            return pl.DataFrame()

    with pytest.raises(ValueError, match="标准数据源.*可用日期范围"):
        VnpyMinuteBacktestService(EmptyRepo()).run(
            VnpyMinuteBacktestConfig(
                symbols=("600000.SH",),
                start=date(2026, 1, 5),
                end=date(2026, 1, 5),
            )
        )
