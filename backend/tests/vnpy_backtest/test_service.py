from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.vnpy_backtest.service import VnpyMinuteBacktestConfig, VnpyMinuteBacktestService


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
    assert result["strategy_info"]["id"] == "opening_breakout_pool"
    assert result["stats"]["symbols_requested"] == 1


def test_vnpy_service_rejects_empty_repository_data() -> None:
    class EmptyRepo:
        def iter_minute_days(self, *args, **kwargs):
            return iter(())

        def minute_trading_days(self, *args, **kwargs):
            return []

        def get_instruments(self):
            return pl.DataFrame()

    with pytest.raises(ValueError, match="没有本地分钟 K"):
        VnpyMinuteBacktestService(EmptyRepo()).run(
            VnpyMinuteBacktestConfig(
                symbols=("600000.SH",),
                start=date(2026, 1, 5),
                end=date(2026, 1, 5),
            )
        )
