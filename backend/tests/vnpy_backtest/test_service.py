from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

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
            signal_price_basis="raw",
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
                signal_price_basis="raw",
            )
        )


def test_vnpy_service_rejects_partial_day_for_etf_strategy() -> None:
    with pytest.raises(ValueError, match="完整交易日 09:30-15:00"):
        VnpyMinuteBacktestService(SimpleNamespace()).run(
            VnpyMinuteBacktestConfig(
                symbols=("159915.SZ",),
                strategy_id="etf_159915_minute",
                start=date(2026, 1, 5),
                end=date(2026, 1, 6),
                start_time=time(10, 0),
            )
        )


def test_instrument_absolute_limit_prices_are_not_used_as_historical_percentages() -> None:
    class MetadataRepo:
        def get_instruments(self):
            return pl.DataFrame({
                "symbol": ["002938.SZ"],
                "name": ["鹏鼎控股"],
                "tick_size": [0.01],
                "limit_up": [92.36],
                "limit_down": [75.56],
            })

    names, tick_sizes = VnpyMinuteBacktestService(MetadataRepo())._instrument_metadata(
        ("002938.SZ",),
    )
    assert names == {"002938.SZ": "鹏鼎控股"}
    assert tick_sizes == {"002938.SZ": 0.01}


def test_daily_market_metadata_uses_historical_raw_pre_close_and_high(tmp_path) -> None:
    part = tmp_path / "kline_daily_xbx" / "date=2026-05-22"
    part.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["002938.SZ"],
        "date": [date(2026, 5, 22)],
        "pre_close": [94.48],
        "open": [95.00],
        "high": [103.93],
        "low": [93.80],
        "close": [101.20],
        "name": ["鹏鼎控股"],
    }).write_parquet(part / "part.parquet")
    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))

    metadata = VnpyMinuteBacktestService(repo)._daily_market_metadata(
        ("002938.SZ",),
        date(2026, 5, 22),
        date(2026, 5, 22),
    )
    assert metadata == {
        date(2026, 5, 22): {
            "002938.SZ": {
                "pre_close": 94.48,
                "price_limit_pct": 0.10,
                "open": 95.00,
                "high": 103.93,
                "low": 93.80,
                "close": 101.20,
            },
        },
    }
