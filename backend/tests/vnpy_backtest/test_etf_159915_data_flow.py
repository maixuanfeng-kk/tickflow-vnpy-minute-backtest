from datetime import date, datetime
from types import SimpleNamespace

import polars as pl

from app.vnpy_backtest.service import VnpyMinuteBacktestService
from app.vnpy_backtest.portfolio import DailyContextBuilder


def _repo(tmp_path):
    return SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))


def test_etf_minute_loader_uses_etf_partitions_and_filters_after_close(tmp_path):
    path = tmp_path / "kline_etf_minute" / "date=2026-07-01"
    path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["159915.SZ", "159915.SZ"],
            "datetime": [datetime(2026, 7, 1, 15, 0), datetime(2026, 7, 1, 15, 6)],
            "open": [3.9, 3.9], "high": [3.9, 3.9], "low": [3.9, 3.9],
            "close": [3.9, 3.9], "volume": [100.0, 100.0], "amount": [390.0, 390.0],
        }
    ).write_parquet(path / "part.parquet")

    service = VnpyMinuteBacktestService(_repo(tmp_path))
    days = service._etf_minute_trading_days(date(2026, 7, 1), date(2026, 7, 1))
    rows = list(service._iter_etf_minute_days(["159915.SZ"], date(2026, 7, 1), date(2026, 7, 1)))

    assert days == [date(2026, 7, 1)]
    assert rows[0][1].height == 1
    assert rows[0][1]["datetime"][0] == datetime(2026, 7, 1, 15, 0)


def test_etf_strategy_is_identified_separately_from_stock_minute_data():
    assert VnpyMinuteBacktestService._is_etf_strategy("etf_159915_minute") is True
    assert VnpyMinuteBacktestService._is_etf_strategy("opening_breakout_pool") is False


def test_daily_context_can_seed_etf_history_before_minute_warmup():
    builder = DailyContextBuilder()
    builder.add_daily_history(
        {
            "159915.SZ": [
                {"date": date(2026, 6, 29), "open": 3.0, "high": 3.1, "low": 2.9, "close": 3.05, "volume": 1000},
                {"date": date(2026, 6, 30), "open": 3.1, "high": 3.2, "low": 3.0, "close": 3.15, "volume": 1100},
            ]
        }
    )
    references = builder.references(date(2026, 7, 1), symbols=["159915.SZ"])
    assert references["159915.SZ"].closes == (3.05, 3.15)
    assert references["159915.SZ"].opens == (3.0, 3.1)
