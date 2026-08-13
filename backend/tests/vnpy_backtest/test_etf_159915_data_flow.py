from datetime import date, datetime, time
from types import SimpleNamespace

import polars as pl

from app.vnpy_backtest.service import VnpyMinuteBacktestService
from app.vnpy_backtest.portfolio import DailyContextBuilder
from app.vnpy_backtest.local_data import bars_from_minute_frame


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


def test_minute_backtest_filters_requested_start_and_end_minutes():
    frame = pl.DataFrame(
        {
            "symbol": ["159915.SZ"] * 4,
            "datetime": [
                datetime(2026, 7, 1, 9, 29),
                datetime(2026, 7, 1, 9, 30),
                datetime(2026, 7, 1, 10, 0),
                datetime(2026, 7, 1, 10, 1),
            ],
            "open": [1.0] * 4,
            "high": [1.0] * 4,
            "low": [1.0] * 4,
            "close": [1.0] * 4,
            "volume": [100.0] * 4,
            "amount": [100.0] * 4,
        }
    )

    filtered = VnpyMinuteBacktestService._filter_requested_minutes(
        frame,
        trading_day=date(2026, 7, 1),
        start_time=time(9, 30),
        end_time=time(10, 0),
    )

    assert filtered["datetime"].to_list() == [
        datetime(2026, 7, 1, 9, 30),
        datetime(2026, 7, 1, 10, 0),
    ]


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


def test_etf_daily_metadata_overrides_minute_aggregated_low(tmp_path):
    trading_day = date(2025, 1, 9)
    daily_path = tmp_path / "kline_etf_daily" / f"date={trading_day.isoformat()}"
    daily_path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["159915.SZ"],
            "date": [trading_day],
            "open": [1.956],
            "high": [1.985],
            "low": [1.954],
            "close": [1.971],
            "pre_close": [1.968],
            "volume": [13_526_000.0],
            "amount": [26_720_000.0],
        }
    ).write_parquet(daily_path / "part.parquet")

    service = VnpyMinuteBacktestService(_repo(tmp_path))
    metadata = service._daily_market_metadata(
        ("159915.SZ",), trading_day, trading_day, etf_data=True,
    )
    minute_frame = pl.DataFrame(
        {
            "symbol": ["159915.SZ"],
            "datetime": [datetime(2025, 1, 9, 15, 0)],
            "open": [1.956],
            "high": [1.985],
            "low": [1.956],
            "close": [1.971],
            "volume": [100.0],
            "amount": [197.1],
        }
    )
    builder = DailyContextBuilder()
    builder.add_day(
        {"159915.SZ": bars_from_minute_frame("159915.SZ", minute_frame)},
        daily_prices=metadata[trading_day],
    )

    reference = builder.references(date(2025, 1, 10), symbols=["159915.SZ"])["159915.SZ"]

    assert reference.previous_low == 1.954
