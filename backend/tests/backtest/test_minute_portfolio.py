from datetime import date, datetime, timedelta

import pytest
import polars as pl

from app.backtest.minute_portfolio import (
    LocalMinuteParquetRepository,
    MinutePortfolioConfig,
    MinutePortfolioEngine,
    MinutePortfolioService,
    OpeningVolumeStrategyParams,
    _load_rows_and_context,
    entry_reason,
    is_in_scan_window,
    rank_candidates,
)


def test_local_parquet_repository_normalizes_tdx_rows_and_builds_daily_ma(tmp_path) -> None:
    pl.DataFrame({
        "ts_code": ["600000.XSHG"] * 3,
        "trade_time": ["2026-01-02 09:30:00", "2026-01-02 09:31:00", "2026-01-05 09:30:00"],
        "open": [10.0, 10.1, 10.2], "high": [10.1, 10.2, 10.3],
        "low": [9.9, 10.0, 10.1], "close": [10.05, 10.15, 10.25],
        "vol": [100, 200, 300], "amount": [1000, 2000, 3000],
    }).write_parquet(tmp_path / "600000.SH.parquet")

    repo = LocalMinuteParquetRepository(tmp_path)
    minutes = repo.get_minute_range(["600000.SH"], date(2026, 1, 2), date(2026, 1, 5))
    daily = repo.get_daily_batch(["600000.SH"], date(2026, 1, 2), date(2026, 1, 5), ["symbol", "date", "ma5"])

    assert minutes.select("symbol").unique().item() == "600000.SH"
    assert minutes.columns == ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
    assert daily.columns == ["symbol", "date", "ma5"]

    class Repo:
        def get_index_daily(self, symbol, start, end, columns):
            return pl.DataFrame()

    messages: list[dict] = []
    MinutePortfolioService(Repo()).run(MinutePortfolioConfig(
        symbols=["600000.SH"], start=date(2026, 1, 5), end=date(2026, 1, 5),
        minute_data_dir=str(tmp_path),
    ), progress_callback=messages.append)
    days = [message["day"] for message in messages]
    assert days == sorted(days)
    assert any("读取分钟数据" in message["date"] for message in messages)
    assert any("撮合交易日" in message["date"] for message in messages)
    assert messages[-1]["day"] == messages[-1]["total"] == 1000


@pytest.mark.parametrize(
    ("volume_ratio", "expected"),
    [
        (1.49, None),
        (1.5, "previous_bearish_breakout"),
    ],
)
def test_entry_reason_requires_same_time_volume_ratio_for_each_branch(
    volume_ratio: float,
    expected: str | None,
) -> None:
    assert entry_reason(
        previous_open=11.0,
        previous_close=10.0,
        previous_change_pct=-0.02,
        today_return=0.01,
        volume_ratio=volume_ratio,
        crossed_previous_high=True,
    ) == expected


def test_entry_reason_requires_today_return_between_three_and_five_percent() -> None:
    assert entry_reason(
        previous_open=10.0,
        previous_close=10.0,
        previous_change_pct=0.04,
        today_return=0.03,
        volume_ratio=1.5,
        crossed_previous_high=False,
    ) is None


def test_entry_reason_requires_volume_and_any_enabled_branch() -> None:
    params = OpeningVolumeStrategyParams(
        volume_multiple=2.0,
        enable_branch_a=False,
        enable_branch_b=True,
        enable_branch_c=True,
    )

    assert entry_reason(
        previous_open=11.0,
        previous_close=10.0,
        previous_change_pct=-0.02,
        today_return=0.01,
        volume_ratio=1.9,
        crossed_previous_high=True,
        params=params,
    ) is None
    assert entry_reason(
        previous_open=11.0,
        previous_close=10.0,
        previous_change_pct=-0.02,
        today_return=0.01,
        volume_ratio=2.0,
        crossed_previous_high=True,
        params=params,
    ) is None
    assert entry_reason(
        previous_open=10.0,
        previous_close=10.0,
        previous_change_pct=0.04,
        today_return=0.04,
        volume_ratio=2.0,
        crossed_previous_high=False,
        params=params,
    ) == "two_day_moderate_rise"


def test_entry_reason_applies_volume_threshold_per_branch() -> None:
    params = OpeningVolumeStrategyParams(
        enable_branch_a=True,
        enable_branch_b=True,
        enable_branch_c=False,
        branch_a_volume_multiple=2.0,
        branch_b_volume_multiple=1.2,
    )

    assert entry_reason(
        previous_open=11.0,
        previous_close=10.0,
        previous_change_pct=-0.02,
        today_return=0.04,
        volume_ratio=1.5,
        crossed_previous_high=True,
        params=params,
    ) == "two_day_moderate_rise"


def test_legacy_volume_switches_cannot_disable_branch_volume_requirement() -> None:
    params = OpeningVolumeStrategyParams.from_mapping({
        "enable_branch_a_volume_filter": False,
        "enable_branch_b_volume_filter": False,
        "enable_branch_c_volume_filter": False,
        "branch_a_volume_multiple": 1.5,
        "branch_b_volume_multiple": 1.5,
        "branch_c_volume_multiple": 1.5,
    })

    assert entry_reason(
        previous_open=11.0,
        previous_close=10.0,
        previous_change_pct=-0.02,
        today_return=0.01,
        volume_ratio=1.49,
        crossed_previous_high=True,
        params=params,
    ) is None
    assert entry_reason(
        previous_open=10.0,
        previous_close=11.0,
        previous_change_pct=0.01,
        today_return=0.04,
        volume_ratio=1.49,
        crossed_previous_high=False,
        params=params,
    ) is None


def test_entry_reason_uses_editable_branch_a_conditions() -> None:
    params = OpeningVolumeStrategyParams(
        enable_branch_a=True,
        enable_branch_b=False,
        enable_branch_c=False,
        branch_a_volume_multiple=1.5,
        branch_a_previous_candle="bullish",
    )

    assert entry_reason(
        previous_open=10.0,
        previous_close=11.0,
        previous_change_pct=0.10,
        today_return=0.01,
        volume_ratio=1.5,
        crossed_previous_high=True,
        params=params,
    ) == "previous_bearish_breakout"


def test_legacy_branch_a_breakout_switch_cannot_disable_breakout_requirement() -> None:
    params = OpeningVolumeStrategyParams.from_mapping({
        "enable_branch_a": True,
        "enable_branch_b": False,
        "enable_branch_c": False,
        "branch_a_volume_multiple": 1.5,
        "branch_a_require_previous_high_breakout": False,
    })

    assert entry_reason(
        previous_open=11.0,
        previous_close=10.0,
        previous_change_pct=-0.02,
        today_return=0.01,
        volume_ratio=1.5,
        crossed_previous_high=False,
        params=params,
    ) is None


def test_entry_reason_uses_editable_branch_b_return_bounds() -> None:
    params = OpeningVolumeStrategyParams(
        enable_branch_a=False,
        enable_branch_b=True,
        enable_branch_c=False,
        branch_b_volume_multiple=0.1,
        branch_b_today_return_min=0.01,
        branch_b_today_return_max=0.02,
        branch_b_previous_return_max=0.0,
    )

    assert entry_reason(
        previous_open=10.0,
        previous_close=10.0,
        previous_change_pct=-0.01,
        today_return=0.015,
        volume_ratio=0.1,
        crossed_previous_high=False,
        params=params,
    ) == "two_day_moderate_rise"


def test_entry_reason_uses_editable_branch_c_conditions() -> None:
    params = OpeningVolumeStrategyParams(
        enable_branch_a=False,
        enable_branch_b=False,
        enable_branch_c=True,
        branch_c_volume_multiple=0.1,
        branch_c_previous_candle="bearish",
        branch_c_previous_return_max=-0.01,
    )

    assert entry_reason(
        previous_open=11.0,
        previous_close=10.0,
        previous_change_pct=-0.02,
        today_return=0.01,
        volume_ratio=0.1,
        crossed_previous_high=False,
        params=params,
    ) == "previous_moderate_rise"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"branch_a_previous_candle": "sideways"}, "branch_a_previous_candle"),
        ({"branch_a_volume_multiple": 0}, "branch_a_volume_multiple"),
        (
            {"branch_b_today_return_min": 0.05, "branch_b_today_return_max": 0.03},
            "branch_b_today_return_min",
        ),
    ],
)
def test_strategy_params_reject_invalid_branch_values(values: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        OpeningVolumeStrategyParams.from_mapping(values)


def test_scan_window_uses_configured_start_and_end_times() -> None:
    params = OpeningVolumeStrategyParams.from_mapping({
        "scan_start_time": "09:35",
        "scan_end_time": "09:45",
    })

    assert is_in_scan_window(datetime(2026, 1, 5, 9, 34).time(), params) is False
    assert is_in_scan_window(datetime(2026, 1, 5, 9, 35).time(), params) is True
    assert is_in_scan_window(datetime(2026, 1, 5, 9, 45).time(), params) is True
    assert is_in_scan_window(datetime(2026, 1, 5, 9, 46).time(), params) is False
    assert entry_reason(
        previous_open=10.0,
        previous_close=10.0,
        previous_change_pct=0.04,
        today_return=0.05,
        volume_ratio=1.5,
        crossed_previous_high=False,
    ) is None


def test_strategy_params_reject_unsupported_ma_exit_period() -> None:
    with pytest.raises(ValueError, match="ma_exit_period must be one of"):
        OpeningVolumeStrategyParams.from_mapping({"ma_exit_period": 6})


def test_rank_candidates_orders_volume_then_return_then_symbol() -> None:
    rows = rank_candidates([
        {"symbol": "000002.SZ", "volume_ratio": 2.0, "today_return": 0.04},
        {"symbol": "000001.SZ", "volume_ratio": 2.0, "today_return": 0.04},
        {"symbol": "600000.SH", "volume_ratio": 1.5, "today_return": 0.09},
    ])

    assert [row["symbol"] for row in rows] == [
        "000001.SZ",
        "000002.SZ",
        "600000.SH",
    ]


def test_engine_fills_top_eight_candidates_at_the_next_minute_open() -> None:
    symbols = [f"00000{i}.SZ" for i in range(1, 10)]
    day = date(2026, 1, 5)
    daily_context = {
        (symbol, day): {
            "previous_open": 11.0,
            "previous_close": 10.0,
            "previous_high": 10.5,
            "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        }
        for symbol in symbols
    }
    rows = []
    for index, symbol in enumerate(symbols):
        rows.extend([
            {
                "symbol": symbol,
                "datetime": datetime(2026, 1, 5, 9, 30),
                "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
                "volume": 150.0 + index, "previous_cumulative_volume": 100.0,
            },
            {
                "symbol": symbol,
                "datetime": datetime(2026, 1, 5, 9, 31),
                "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
                "volume": 100.0, "previous_cumulative_volume": 200.0,
            },
        ])

    result = MinutePortfolioEngine(MinutePortfolioConfig(symbols=symbols)).run(
        rows,
        daily_context,
    )

    assert len(result["trades"]) == 8
    assert all(trade["entry_datetime"].endswith("09:31:00") for trade in result["trades"])
    assert all(trade["shares"] % 100 == 0 for trade in result["trades"])


def _single_entry_trade(*, execution_volume: float, **config_values):
    symbol = "600000.SH"
    day = date(2026, 1, 5)
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": execution_volume, "previous_cumulative_volume": 200.0,
        },
    ]
    contexts = {
        (symbol, day): {
            "previous_open": 11.0,
            "previous_close": 10.0,
            "previous_high": 10.5,
            "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        },
    }
    config = MinutePortfolioConfig(
        symbols=[symbol],
        initial_capital=100_000.0,
        max_positions=1,
        commission_pct=0.0,
        stamp_tax_pct=0.0,
        slippage_bps=0.0,
        **config_values,
    )
    return MinutePortfolioEngine(config).run(rows, contexts)["trades"][0]


def test_engine_default_buy_sizing_remains_unconstrained() -> None:
    trade = _single_entry_trade(execution_volume=20_000.0)

    assert trade["shares"] == 10_000


def test_engine_reserves_three_percent_cash_on_each_buy() -> None:
    trade = _single_entry_trade(
        execution_volume=20_000.0,
        cash_reserve_ratio=0.03,
    )

    assert trade["shares"] == 9_700


def test_engine_caps_buy_shares_at_execution_minute_volume() -> None:
    trade = _single_entry_trade(
        execution_volume=550.0,
        max_buy_volume_ratio=1.0,
    )

    assert trade["shares"] == 500


def test_engine_uses_stricter_cash_or_volume_buy_limit() -> None:
    trade = _single_entry_trade(
        execution_volume=550.0,
        cash_reserve_ratio=0.03,
        max_buy_volume_ratio=1.0,
    )

    assert trade["shares"] == 500


def test_load_context_keeps_four_previous_closes_for_intraday_ma5() -> None:
    symbol = "600000.SH"
    dates = [date(2026, 1, day) for day in (2, 5, 6, 7, 8)]

    class Repo:
        def get_daily_batch(self, symbols, start, end, columns):
            return pl.DataFrame({
                "symbol": [symbol] * 5,
                "date": dates,
                "open": [10.0, 10.5, 11.0, 11.5, 12.0],
                "high": [10.2, 10.7, 11.2, 11.7, 12.2],
                "close": [10.0, 10.5, 11.0, 11.5, 12.0],
                "ma5": [None, None, None, None, 11.0],
            })

        def get_minute_range(self, symbols, start, end, asset_type):
            return pl.DataFrame({
                "symbol": [symbol],
                "datetime": [datetime(2026, 1, 8, 9, 30)],
                "open": [12.0], "high": [12.1], "low": [11.9], "close": [12.0],
                "volume": [100.0], "amount": [1_200.0],
            })

    _, contexts = _load_rows_and_context(
        Repo(), [symbol], date(2026, 1, 8), date(2026, 1, 8), 5,
    )

    assert contexts[(symbol, date(2026, 1, 8))]["previous_closes"] == [
        10.0, 10.5, 11.0, 11.5,
    ]


def _two_day_ma_exit_trade(previous_closes: list[float], previous_ma5: float):
    symbol = "600000.SH"
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
            "volume": 20_000.0, "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 9, 30),
            "open": 10.1, "high": 10.2, "low": 10.0, "close": 10.1,
            "volume": 1_000.0, "previous_cumulative_volume": 500.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 9, 31),
            "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
            "volume": 1_000.0, "previous_cumulative_volume": 600.0,
        },
    ]
    contexts = {
        (symbol, date(2026, 1, 5)): {
            "previous_open": 11.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        },
        (symbol, date(2026, 1, 6)): {
            "previous_open": 10.0, "previous_close": 10.0,
            "previous_high": 10.1, "previous_change_pct": 0.0,
            "previous_ma5": previous_ma5,
            "previous_closes": previous_closes,
        },
    }
    config = MinutePortfolioConfig(
        symbols=[symbol],
        initial_capital=100_000.0,
        max_positions=1,
        commission_pct=0.0,
        stamp_tax_pct=0.0,
        slippage_bps=0.0,
    )
    return MinutePortfolioEngine(config).run(rows, contexts)["trades"][0]


def test_engine_uses_current_minute_close_in_intraday_ma5_exit() -> None:
    trade = _two_day_ma_exit_trade(
        previous_closes=[11.0, 11.0, 11.0, 11.0],
        previous_ma5=9.0,
    )

    assert trade["exit_reason"] == "ma5_breakdown"
    assert trade["exit_datetime"].endswith("09:31:00")


def test_engine_skips_intraday_ma5_exit_without_four_previous_closes() -> None:
    trade = _two_day_ma_exit_trade(
        previous_closes=[11.0, 11.0, 11.0],
        previous_ma5=11.0,
    )

    assert trade["exit_reason"] == "end_of_backtest"


def test_engine_closes_positions_at_the_end_of_the_backtest() -> None:
    day = date(2026, 1, 5)
    rows = [
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
            "volume": 100.0, "previous_cumulative_volume": 200.0,
        },
    ]
    contexts = {
        ("600000.SH", day): {
            "previous_open": 11.0,
            "previous_close": 10.0,
            "previous_high": 10.5,
            "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        },
    }

    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=["600000.SH"], max_positions=1,
    )).run(rows, contexts)

    assert result["trades"][-1]["exit_reason"] == "end_of_backtest"
    assert result["trades"][-1]["exit_datetime"].endswith("09:31:00")


def test_engine_returns_net_trade_pnl_and_daily_equity() -> None:
    rows = [
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
            "volume": 100.0, "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 6, 15, 30),
            "open": 10.8, "high": 11.0, "low": 10.7, "close": 10.9,
            "volume": 100.0, "previous_cumulative_volume": 100.0,
        },
    ]
    contexts = {
        ("600000.SH", date(2026, 1, 5)): {
            "previous_open": 11.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        },
        ("600000.SH", date(2026, 1, 6)): {
            "previous_open": 10.0, "previous_close": 10.3,
            "previous_high": 10.4, "previous_change_pct": 0.03,
            "previous_ma5": 9.0,
        },
    }

    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=["600000.SH"], initial_capital=1_000_000.0, max_positions=1,
    )).run(rows, contexts)

    trade = result["trades"][0]
    unit_cost = trade["entry_cost"] / trade["shares"]
    assert trade["entry_date"] == "2026-01-05"
    assert trade["exit_date"] == "2026-01-06"
    assert trade["max_floating_gain_pct"] == pytest.approx(
        max(0.0, 11.0 / unit_cost - 1.0), abs=1e-6,
    )
    assert trade["max_floating_loss_pct"] == pytest.approx(
        min(0.0, 10.2 / unit_cost - 1.0), abs=1e-6,
    )
    assert trade["pnl_amount"] == pytest.approx(trade["pnl_pct"] * trade["entry_cost"], abs=0.1)
    assert trade["duration"] == 1
    assert [row["date"] for row in result["equity_curve"]] == ["2026-01-05", "2026-01-06"]
    assert result["equity_curve"][-1]["value"] == pytest.approx(result["cash"], abs=0.01)
    assert result["drawdown_curve"][-1]["value"] <= 0


def test_engine_excludes_exit_minute_prices_from_holding_excursions() -> None:
    rows = [
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0,
            "volume": 100.0, "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 6, 9, 30),
            "open": 10.0, "high": 12.0, "low": 8.0, "close": 8.0,
            "volume": 100.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 7, 9, 30),
            "open": 8.0, "high": 100.0, "low": 0.1, "close": 8.0,
            "volume": 100.0, "previous_cumulative_volume": 100.0,
        },
    ]
    contexts = {
        ("600000.SH", date(2026, 1, 5)): {
            "previous_open": 11.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        },
        ("600000.SH", date(2026, 1, 6)): {
            "previous_open": 10.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": 0.0,
            "previous_ma5": 1.0,
        },
    }

    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=["600000.SH"], initial_capital=1_000_000.0, max_positions=1,
    )).run(rows, contexts)

    trade = result["trades"][0]
    unit_cost = trade["entry_cost"] / trade["shares"]
    assert trade["exit_date"] == "2026-01-07"
    assert trade["max_floating_gain_pct"] == pytest.approx(
        max(0.0, 12.0 / unit_cost - 1.0), abs=1e-6,
    )
    assert trade["max_floating_loss_pct"] == pytest.approx(
        min(0.0, 8.0 / unit_cost - 1.0), abs=1e-6,
    )


def test_service_reads_daily_and_minute_rows_and_returns_backtest_shape() -> None:
    class Repo:
        minute_start = None

        def get_name_map(self, symbols):
            assert symbols == ["600000.SH"]
            return {"600000.SH": "浦发银行"}

        def get_daily_batch(self, symbols, start, end, columns):
            return __import__("polars").DataFrame({
                "symbol": ["600000.SH", "600000.SH"],
                "date": [date(2026, 1, 2), date(2026, 1, 5)],
                "open": [11.0, 10.0], "high": [10.5, 10.4],
                "close": [10.0, 10.3], "ma5": [9.0, 9.1],
            })

        def get_minute_range(self, symbols, start, end, asset_type):
            self.minute_start = start
            return __import__("polars").DataFrame({
                "symbol": ["600000.SH"] * 4,
                "datetime": [
                    datetime(2026, 1, 2, 9, 30), datetime(2026, 1, 2, 9, 31),
                    datetime(2026, 1, 5, 9, 30), datetime(2026, 1, 5, 9, 31),
                ],
                "open": [10.0, 10.0, 10.0, 10.3], "high": [10.0, 10.0, 10.6, 10.4],
                "low": [10.0, 10.0, 10.0, 10.2], "close": [10.0, 10.0, 10.2, 10.3],
                "volume": [100.0, 100.0, 150.0, 100.0], "amount": [1000.0] * 4,
            })

        def get_index_daily(self, symbol, start, end, columns):
            assert symbol == "000001.XSHG"
            return __import__("polars").DataFrame({
                "date": [date(2026, 1, 5), date(2026, 1, 6)],
                "close": [100.0, 101.0],
            })

    repo = Repo()
    result = MinutePortfolioService(repo).run(MinutePortfolioConfig(
        symbols=["600000.XSHG"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        initial_capital=2_000_000.0,
        max_positions=4,
        cash_reserve_ratio=0.03,
        max_buy_volume_ratio=1.0,
    ))

    assert result["config"]["engine"] == "minute_portfolio"
    assert result["config"]["symbols"] == ["600000.XSHG"]
    assert result["config"]["initial_capital"] == 2_000_000.0
    assert result["config"]["max_positions"] == 4
    assert result["config"]["cash_reserve_ratio"] == 0.03
    assert result["config"]["max_buy_volume_ratio"] == 1.0
    assert result["stats"]["total_trade_count"] == 1
    assert {
        "total_return", "annual_return", "sharpe", "sortino", "max_drawdown",
        "mc_maxdd_p50", "mc_maxdd_p95", "win_rate", "n_trades", "final_equity",
        "total_trade_count", "end_balance",
    } <= result["stats"].keys()
    assert result["equity_curve"]
    assert result["drawdown_curve"]
    assert result["benchmark_curve"] == [
        {"date": "2026-01-05", "close": 100.0},
        {"date": "2026-01-06", "close": 101.0},
    ]
    assert result["per_symbol_stats"][0]["symbol"] == "600000.SH"
    assert result["trades"][0]["name"] == "浦发银行"
    assert result["per_symbol_stats"][0]["name"] == "浦发银行"
    assert result["per_symbol_stats"][0]["best"] == result["trades"][0]["max_floating_gain_pct"]
    assert result["per_symbol_stats"][0]["worst"] == result["trades"][0]["max_floating_loss_pct"]
    assert repo.minute_start <= date(2026, 1, 2)


def test_per_symbol_best_and_worst_use_holding_excursions() -> None:
    rows = MinutePortfolioService._per_symbol([
        {
            "symbol": "600000.SH", "pnl_pct": 0.08,
            "max_floating_gain_pct": 0.15, "max_floating_loss_pct": -0.03,
        },
        {
            "symbol": "600000.SH", "pnl_pct": -0.02,
            "max_floating_gain_pct": 0.05, "max_floating_loss_pct": -0.12,
        },
    ])

    assert rows == [{
        "symbol": "600000.SH",
        "n_trades": 2,
        "total_return": round((1.08 * 0.98) - 1.0, 6),
        "win_rate": 0.5,
        "best": 0.15,
        "worst": -0.12,
    }]


def test_service_excludes_warmup_minutes_from_execution(monkeypatch) -> None:
    captured = {}

    class Repo:
        def get_daily_batch(self, symbols, start, end, columns):
            return __import__("polars").DataFrame({
                "symbol": ["600000.SH", "600000.SH"],
                "date": [date(2026, 1, 2), date(2026, 1, 5)],
                "open": [10.0, 10.0], "high": [10.0, 10.0],
                "close": [10.0, 10.0], "ma5": [10.0, 10.0],
            })

        def get_minute_range(self, symbols, start, end, asset_type):
            return __import__("polars").DataFrame({
                "symbol": ["600000.SH", "600000.SH"],
                "datetime": [
                    datetime(2026, 1, 2, 9, 30),
                    datetime(2026, 1, 5, 9, 30),
                ],
                "open": [10.0, 10.0], "high": [10.0, 10.0],
                "low": [10.0, 10.0], "close": [10.0, 10.0],
                "volume": [100.0, 100.0], "amount": [1000.0, 1000.0],
            })

    class Engine:
        def __init__(self, config):
            pass

        def run(self, rows, contexts, progress_callback=None):
            captured["dates"] = {row["datetime"].date() for row in rows}
            return {"cash": 1_000_000.0, "trades": []}

    monkeypatch.setattr("app.backtest.minute_portfolio.MinutePortfolioEngine", Engine)
    MinutePortfolioService(Repo()).run(MinutePortfolioConfig(
        symbols=["600000.SH"], start=date(2026, 1, 5), end=date(2026, 1, 5),
    ))

    assert captured["dates"] == {date(2026, 1, 5)}
