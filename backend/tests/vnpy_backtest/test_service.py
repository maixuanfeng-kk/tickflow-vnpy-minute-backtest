from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.vnpy_backtest.service import VnpyMinuteBacktestConfig, VnpyMinuteBacktestService


class _Repo:
    def get_minute_range(self, symbols, start, end, asset_type="stock"):
        assert symbols == ["600000.SH"]
        assert asset_type == "stock"
        base = datetime(2026, 1, 5, 9, 30)
        return pl.DataFrame(
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


def test_vnpy_service_replays_repository_minute_bars() -> None:
    result = VnpyMinuteBacktestService(_Repo()).run(
        VnpyMinuteBacktestConfig(
            symbol="600000.SH",
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            params={"amount_multiple": 0.1},
        )
    )

    assert result["config"]["engine"] == "vnpy"
    assert result["config"]["frequency"] == "1m"
    assert result["strategy_info"]["id"] == "minute_double_ma_volume"
    assert result["stats"]["total_trade_count"] == 1


def test_vnpy_service_rejects_empty_repository_data() -> None:
    class EmptyRepo:
        def get_minute_range(self, *args, **kwargs):
            return pl.DataFrame()

    with pytest.raises(ValueError, match="no minute bars"):
        VnpyMinuteBacktestService(EmptyRepo()).run(
            VnpyMinuteBacktestConfig(
                symbol="600000.SH",
                start=date(2026, 1, 5),
                end=date(2026, 1, 5),
            )
        )
