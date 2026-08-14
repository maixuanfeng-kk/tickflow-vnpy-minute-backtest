from datetime import date, datetime, time
from types import SimpleNamespace

import polars as pl

from app.vnpy_backtest.service import VnpyMinuteBacktestConfig, VnpyMinuteBacktestService
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


def test_etf_warmup_start_uses_tenth_prior_daily_trading_day(tmp_path):
    trading_days = [date(2025, 1, day) for day in range(2, 12)]
    for trading_day in trading_days:
        path = tmp_path / "kline_etf_daily" / f"date={trading_day.isoformat()}"
        path.mkdir(parents=True)
        pl.DataFrame(
            {
                "symbol": ["159915.SZ"],
                "date": [trading_day],
                "open": [2.0],
                "high": [2.1],
                "low": [1.9],
                "close": [2.0],
                "pre_close": [2.0],
            }
        ).write_parquet(path / "part.parquet")
    other_path = tmp_path / "kline_etf_daily" / "date=2024-12-01"
    other_path.mkdir(parents=True)
    pl.DataFrame(
        {
            "symbol": ["159916.SZ"],
            "date": [date(2024, 12, 1)],
            "open": [2.0],
            "high": [2.1],
            "low": [1.9],
            "close": [2.0],
            "pre_close": [2.0],
        }
    ).write_parquet(other_path / "part.parquet")

    service = VnpyMinuteBacktestService(_repo(tmp_path))

    assert service._etf_warmup_start(("159915.SZ",), date(2025, 2, 10)) == trading_days[0]


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


def test_daily_context_merges_warmup_minutes_into_seeded_daily_row():
    trading_day = date(2026, 6, 30)
    builder = DailyContextBuilder()
    builder.add_daily_history(
        {
            "159915.SZ": [
                {"date": trading_day, "open": 3.1, "high": 3.2, "low": 3.0, "close": 3.15, "volume": 1_100},
            ]
        }
    )
    minute_frame = pl.DataFrame(
        {
            "symbol": ["159915.SZ", "159915.SZ"],
            "datetime": [datetime(2026, 6, 30, 9, 30), datetime(2026, 6, 30, 9, 31)],
            "open": [3.1, 3.12], "high": [3.12, 3.15], "low": [3.09, 3.11],
            "close": [3.12, 3.15], "volume": [100.0, 150.0], "amount": [312.0, 472.5],
        }
    )
    builder.add_day(
        {"159915.SZ": bars_from_minute_frame("159915.SZ", minute_frame)},
        daily_prices={
            "159915.SZ": {"open": 3.1, "high": 3.2, "low": 3.0, "close": 3.15},
        },
    )

    reference = builder.references(date(2026, 7, 1), symbols=["159915.SZ"])["159915.SZ"]

    assert reference.closes == (3.15,)
    assert reference.previous_cumulative_volumes == {time(9, 30): 100.0, time(9, 31): 250.0}


def test_etf_first_backtest_day_uses_daily_history_when_warmup_minutes_are_absent(tmp_path):
    prior_days = [
        date(2022, 12, 19), date(2022, 12, 20), date(2022, 12, 21), date(2022, 12, 22),
        date(2022, 12, 23), date(2022, 12, 26), date(2022, 12, 27), date(2022, 12, 28),
        date(2022, 12, 29), date(2022, 12, 30),
    ]
    closes = [2.280] * 8 + [2.277, 2.279]
    for index, trading_day in enumerate(prior_days):
        path = tmp_path / "kline_etf_daily" / f"date={trading_day.isoformat()}"
        path.mkdir(parents=True)
        open_price = 2.287 if trading_day == date(2022, 12, 30) else closes[index]
        pl.DataFrame(
            {
                "symbol": ["159915.SZ"], "date": [trading_day],
                "open": [open_price], "high": [max(open_price, closes[index]) + 0.01],
                "low": [min(open_price, closes[index]) - 0.01], "close": [closes[index]],
                "pre_close": [closes[index - 1] if index else closes[index]],
                "volume": [1_000.0], "amount": [2_280.0],
            }
        ).write_parquet(path / "part.parquet")

    signal_day = date(2023, 1, 3)
    minute_path = tmp_path / "kline_etf_minute" / f"date={signal_day.isoformat()}"
    minute_path.mkdir(parents=True)
    minute_times = [time(9, 30), time(9, 31), time(9, 32), time(11, 28), time(11, 29)]
    pl.DataFrame(
        {
            "symbol": ["159915.SZ"] * 5,
            "datetime": [datetime.combine(signal_day, value) for value in minute_times],
            "open": [2.267, 2.266, 2.269, 2.278, 2.279],
            "high": [2.267, 2.271, 2.269, 2.279, 2.282],
            "low": [2.267, 2.263, 2.260, 2.278, 2.279],
            "close": [2.267, 2.264, 2.263, 2.279, 2.281],
            "volume": [10_000.0] * 5, "amount": [22_790.0] * 5,
        }
    ).write_parquet(minute_path / "part.parquet")

    result = VnpyMinuteBacktestService(_repo(tmp_path)).run(
        VnpyMinuteBacktestConfig(
            start=signal_day, end=signal_day, symbols=("159915.SZ",),
            strategy_id="etf_159915_minute", signal_price_basis="raw",
            commission_pct=0.0, slippage_bps=0.0,
        )
    )

    assert [
        (row["timestamp"], row["reason"])
        for row in result["signal_diagnostics"]
    ] == [("2023-01-03 11:28:00", "4_2_2")]


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
