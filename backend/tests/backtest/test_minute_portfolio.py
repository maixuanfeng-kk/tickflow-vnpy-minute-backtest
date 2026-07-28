from datetime import date, datetime, timedelta

import pytest
import polars as pl

from app.backtest.minute_portfolio import (
    LocalMinuteParquetRepository,
    MinutePortfolioConfig,
    MinutePortfolioEngine,
    MinutePortfolioService,
    OpeningVolumeScanConfig,
    OpeningVolumeScanService,
    OpeningVolumeStrategyParams,
    _buy_block_reason,
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


def test_local_daily_aggregation_starts_with_0925_auction(tmp_path) -> None:
    symbol = "600000.SH"
    pl.DataFrame({
        "ts_code": ["600000.XSHG"] * 4,
        "trade_time": [
            "2026-01-05 09:15:00", "2026-01-05 09:24:00",
            "2026-01-05 09:25:00", "2026-01-05 09:30:00",
        ],
        "open": [9.0, 9.1, 10.0, 10.1],
        "high": [99.0, 98.0, 10.2, 10.3],
        "low": [1.0, 2.0, 9.8, 10.0],
        "close": [9.0, 9.1, 10.1, 10.2],
        "vol": [1_000.0, 2_000.0, 100.0, 200.0],
        "amount": [9_000.0, 18_200.0, 1_010.0, 2_040.0],
    }).write_parquet(tmp_path / f"{symbol}.parquet")

    daily = LocalMinuteParquetRepository(tmp_path).get_daily_batch(
        [symbol], date(2026, 1, 5), date(2026, 1, 5),
        ["symbol", "date", "open", "high", "low", "close", "volume"],
    ).row(0, named=True)

    assert daily == {
        "symbol": symbol, "date": date(2026, 1, 5),
        "open": 10.0, "high": 10.3, "low": 9.8,
        "close": 10.2, "volume": 300.0,
    }


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


def test_rank_candidates_scores_with_weights_and_applies_score_bounds() -> None:
    rows = rank_candidates(
        [
            {"symbol": "600000.SH", "volume_ratio": 3.0, "today_return": 0.01},
            {"symbol": "600001.SH", "volume_ratio": 2.0, "today_return": 0.04},
            {"symbol": "600002.SH", "volume_ratio": 1.5, "today_return": 0.02},
        ],
        mode="score",
        weights={"volume_ratio": 0.0, "today_return": 1.0},
        score_min=60.0,
    )

    assert [row["symbol"] for row in rows] == ["600001.SH"]


def test_rank_candidates_follows_watchlist_order_then_symbol() -> None:
    rows = rank_candidates(
        [
            {"symbol": "600002.SH", "volume_ratio": 3.0, "today_return": 0.04},
            {"symbol": "600003.SH", "volume_ratio": 2.0, "today_return": 0.03},
            {"symbol": "600001.SH", "volume_ratio": 1.5, "today_return": 0.02},
        ],
        mode="watchlist_order",
        watchlist_order=["600001.SH", "600002.SH"],
    )

    assert [row["symbol"] for row in rows] == [
        "600001.SH",
        "600002.SH",
        "600003.SH",
    ]


def test_rank_candidates_keeps_duplicate_watchlist_symbols_ahead_of_unlisted_symbols() -> None:
    rows = rank_candidates(
        [
            {"symbol": "000001.SZ", "volume_ratio": 3.0, "today_return": 0.04},
            {"symbol": "600003.SH", "volume_ratio": 2.0, "today_return": 0.03},
            {"symbol": "600002.SH", "volume_ratio": 1.5, "today_return": 0.02},
            {"symbol": "600001.SH", "volume_ratio": 1.0, "today_return": 0.01},
        ],
        mode="watchlist_order",
        watchlist_order=["600001.SH", "600001.SH", "600003.SH"],
    )

    assert [row["symbol"] for row in rows] == [
        "600001.SH",
        "600003.SH",
        "000001.SZ",
        "600002.SH",
    ]


def test_rank_candidates_scores_mixed_weights_with_max_bound_and_full_tie_break() -> None:
    rows = rank_candidates(
        [
            {"symbol": "600006.SH", "volume_ratio": 3.0, "today_return": 0.03, "previous_return": 0.03},
            {"symbol": "600005.SH", "volume_ratio": 3.0, "today_return": 0.01, "previous_return": 0.01},
            {"symbol": "600004.SH", "volume_ratio": 2.0, "today_return": 0.03, "previous_return": 0.01},
            {"symbol": "600003.SH", "volume_ratio": 2.0, "today_return": 0.01, "previous_return": 0.03},
            {"symbol": "600002.SH", "volume_ratio": 2.0, "today_return": 0.01, "previous_return": 0.03},
            {"symbol": "600001.SH", "volume_ratio": 1.0, "today_return": 0.03, "previous_return": 0.03},
            {"symbol": "600000.SH", "volume_ratio": 1.5, "today_return": 0.01, "previous_return": 0.01},
        ],
        mode="score",
        weights={"volume_ratio": 0.5, "today_return": 0.25, "previous_return": 0.25},
        score_max=50.0,
    )

    assert [row["symbol"] for row in rows] == [
        "600005.SH",
        "600004.SH",
        "600002.SH",
        "600003.SH",
        "600001.SH",
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


def _opening_candidates(specs: list[tuple[str, float, float, float]]) -> tuple[list[dict], dict]:
    day = date(2026, 1, 5)
    rows: list[dict] = []
    contexts = {}
    for symbol, volume_ratio, today_return, cumulative_amount in specs:
        close = 10.0 * (1 + today_return)
        rows.extend([
            {
                "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
                "open": close, "high": 10.6, "low": close, "close": close,
                "volume": volume_ratio * 100, "cumulative_volume": volume_ratio * 100,
                "previous_cumulative_volume": 100.0,
                "amount": cumulative_amount, "cumulative_amount": cumulative_amount,
            },
            {
                "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
                "open": close, "high": close, "low": close, "close": close,
                "volume": 10_000.0, "previous_cumulative_volume": 200.0,
                "amount": 100_000.0,
            },
        ])
        contexts[(symbol, day)] = {
            "previous_open": 11.0,
            "previous_close": 10.0,
            "previous_high": 10.5,
            "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        }
    return rows, contexts


def test_engine_defaults_to_volume_ratio_candidate_sort() -> None:
    specs = [
        ("600000.SH", 3.0, 0.01, 100_000_000.0),
        ("600001.SH", 2.0, 0.04, 100_000_000.0),
    ]
    rows, contexts = _opening_candidates(specs)

    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol for symbol, *_ in specs],
        max_positions=1,
    )).run(rows, contexts)

    assert [trade["symbol"] for trade in result["trades"]] == ["600000.SH"]


def test_opening_volume_scan_defaults_to_volume_ratio_candidate_sort(monkeypatch) -> None:
    day = date(2026, 1, 5)
    monkeypatch.setattr(
        "app.backtest.minute_portfolio.watchlist.list_symbols",
        lambda: [{"symbol": "600000.SH"}, {"symbol": "600001.SH"}],
    )
    rows = [
        {
            "symbol": "600000.SH", "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.2, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 200.0, "cumulative_volume": 200.0,
            "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": "600001.SH", "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.2, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 300.0, "cumulative_volume": 300.0,
            "previous_cumulative_volume": 100.0,
        },
    ]
    contexts = {
        (symbol, day): {
            "previous_open": 11.0,
            "previous_close": 10.0,
            "previous_high": 10.5,
            "previous_change_pct": -0.02,
        }
        for symbol in ["600000.SH", "600001.SH"]
    }
    monkeypatch.setattr(
        "app.backtest.minute_portfolio._load_rows_and_context",
        lambda *args: (rows, contexts),
    )

    result = OpeningVolumeScanService(object()).run(OpeningVolumeScanConfig(
        as_of=day,
        strategy_params=OpeningVolumeStrategyParams(),
    ))

    assert [row["symbol"] for row in result["rows"]] == ["600001.SH", "600000.SH"]


def test_engine_applies_supported_basic_filters_before_entry() -> None:
    specs = [
        ("600000.SH", 2.0, 0.02, 200_000_000.0),
        ("600001.SH", 2.0, 0.02, 200_000_000.0),
        ("000001.SZ", 2.0, 0.02, 200_000_000.0),
        ("600002.SH", 2.0, -0.60, 200_000_000.0),
        ("600003.SH", 2.0, 0.02, 50_000_000.0),
    ]
    rows, contexts = _opening_candidates(specs)
    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol for symbol, *_ in specs],
        max_positions=5,
        basic_filter={
            "enabled": True,
            "price_min": 5.0,
            "amount_min": 100_000_000.0,
            "boards": ["沪主板"],
            "exclude_st": True,
        },
        symbol_names={"600001.SH": "*ST 示例"},
    )).run(rows, contexts)

    assert [trade["symbol"] for trade in result["trades"]] == ["600000.SH"]


def test_engine_scores_same_minute_candidates_and_applies_score_bounds() -> None:
    specs = [
        ("600000.SH", 3.0, 0.01, 100_000_000.0),
        ("600001.SH", 2.0, 0.04, 100_000_000.0),
        ("600002.SH", 1.5, 0.02, 100_000_000.0),
    ]
    rows, contexts = _opening_candidates(specs)
    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol for symbol, *_ in specs],
        max_positions=1,
        candidate_sort="score",
        scoring={"volume_ratio": 0.0, "today_return": 1.0},
        score_min=60.0,
    )).run(rows, contexts)

    assert [trade["symbol"] for trade in result["trades"]] == ["600001.SH"]


def test_engine_uses_candidate_sort_watchlist_order_for_same_minute_candidates() -> None:
    specs = [
        ("600000.SH", 3.0, 0.01, 100_000_000.0),
        ("600001.SH", 2.0, 0.04, 100_000_000.0),
    ]
    rows, contexts = _opening_candidates(specs)

    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=["600001.SH", "600000.SH"],
        max_positions=1,
        candidate_sort="watchlist_order",
    )).run(rows, contexts)

    assert [trade["symbol"] for trade in result["trades"]] == ["600001.SH"]


def test_engine_does_not_score_or_filter_when_candidates_fit_available_slots() -> None:
    specs = [("600000.SH", 2.0, 0.02, 100_000_000.0)]
    rows, contexts = _opening_candidates(specs)

    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=["600000.SH"],
        max_positions=1,
        candidate_sort="score",
        scoring={"volume_ratio": 1.0},
        score_min=60.0,
    )).run(rows, contexts)

    assert [trade["symbol"] for trade in result["trades"]] == ["600000.SH"]


def _breakout_result(highs: tuple[float, float]):
    symbol = "600000.SH"
    day = date(2026, 1, 5)
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": highs[0], "low": 10.0, "close": 10.2,
            "volume": 100.0, "cumulative_volume": 100.0,
            "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.2, "high": highs[1], "low": 10.1, "close": 10.3,
            "volume": 200.0, "cumulative_volume": 300.0,
            "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 32),
            "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
            "volume": 10_000.0, "cumulative_volume": 10_300.0,
            "previous_cumulative_volume": 300.0,
        },
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": 10.0,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_ma5": 9.0,
    }}
    return MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1,
        strategy_params=OpeningVolumeStrategyParams(
            enable_branch_b=False,
            enable_branch_c=False,
        ),
    )).run(rows, contexts)


def test_engine_breakout_does_not_require_first_cross_from_below() -> None:
    result = _breakout_result((10.6, 10.7))

    assert len(result["trades"]) == 1
    assert result["trades"][0]["entry_datetime"].endswith("09:32:00")


def test_engine_breakout_requires_strictly_greater_high() -> None:
    assert _breakout_result((10.4, 10.5))["trades"] == []


def _ten_equal_portfolio_entries() -> list[dict]:
    symbols = [f"{index:06d}.SZ" for index in range(1, 11)]
    day = date(2026, 1, 5)
    rows = []
    contexts = {}
    for symbol in symbols:
        rows.extend([
            {
                "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
                "open": 1.0, "high": 1.06, "low": 1.0, "close": 1.02,
                "volume": 150.0, "previous_cumulative_volume": 100.0,
            },
            {
                "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
                "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                "volume": 100_000.0, "previous_cumulative_volume": 200.0,
            },
        ])
        contexts[(symbol, day)] = {
            "previous_open": 1.1,
            "previous_close": 1.0,
            "previous_high": 1.05,
            "previous_change_pct": -0.02,
            "previous_ma5": 0.9,
        }

    config = MinutePortfolioConfig(
        symbols=symbols,
        initial_capital=100_000.0,
        max_positions=10,
        cash_reserve_ratio=0.03,
        commission_pct=0.0,
        stamp_tax_pct=0.0,
        slippage_bps=0.0,
    )
    return MinutePortfolioEngine(config).run(rows, contexts)["trades"]


def test_engine_limits_total_position_targets_to_97_percent_of_equity() -> None:
    trades = _ten_equal_portfolio_entries()

    assert len(trades) == 10
    assert all(trade["shares"] == 9_700 for trade in trades)
    assert sum(trade["entry_cost"] for trade in trades) == 97_000.0


def _two_day_entries_with_marked_gain(*, cash_reserve_ratio: float = 0.03) -> list[dict]:
    first_symbol = "000001.SZ"
    second_symbol = "000002.SZ"
    rows = [
        {
            "symbol": first_symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": first_symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": 1_000.0, "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": first_symbol, "datetime": datetime(2026, 1, 6, 9, 30),
            "open": 20.0, "high": 20.0, "low": 20.0, "close": 20.0,
            "volume": 1_000.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": second_symbol, "datetime": datetime(2026, 1, 6, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": second_symbol, "datetime": datetime(2026, 1, 6, 9, 31),
            "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
            "volume": 10_000.0, "previous_cumulative_volume": 200.0,
        },
    ]
    contexts = {
        (first_symbol, date(2026, 1, 5)): {
            "previous_open": 11.0,
            "previous_close": 10.0,
            "previous_high": 10.5,
            "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        },
        (second_symbol, date(2026, 1, 6)): {
            "previous_open": 11.0,
            "previous_close": 10.0,
            "previous_high": 10.5,
            "previous_change_pct": -0.02,
            "previous_ma5": 9.0,
        },
    }
    config = MinutePortfolioConfig(
        symbols=[first_symbol, second_symbol],
        initial_capital=100_000.0,
        max_positions=2,
        cash_reserve_ratio=cash_reserve_ratio,
        max_buy_volume_ratio=1.0,
        commission_pct=0.0,
        stamp_tax_pct=0.0,
        slippage_bps=0.0,
    )
    return MinutePortfolioEngine(config).run(rows, contexts)["trades"]


def test_engine_position_target_follows_current_marked_equity() -> None:
    trades = _two_day_entries_with_marked_gain()

    second_trade = next(trade for trade in trades if trade["symbol"] == "000002.SZ")
    assert second_trade["shares"] == 5_300
    assert second_trade["entry_cost"] == 53_000.0


def test_engine_zero_reserve_still_uses_current_marked_equity() -> None:
    trades = _two_day_entries_with_marked_gain(cash_reserve_ratio=0.0)

    second_trade = next(trade for trade in trades if trade["symbol"] == "000002.SZ")
    assert second_trade["shares"] == 5_500
    assert second_trade["entry_cost"] == 55_000.0


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


def _risk_control_trade(
    *,
    day_one_high: float = 10.2,
    day_two_close: float = 10.0,
    stop_loss_pct: float = 0.5,
    **config_overrides,
) -> dict:
    symbol = "600000.SH"
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": day_one_high, "low": 10.0, "close": 10.1,
            "volume": 10_000.0, "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 9, 30),
            "open": day_two_close, "high": day_two_close, "low": day_two_close,
            "close": day_two_close, "volume": 1_000.0,
            "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 9, 31),
            "open": day_two_close, "high": day_two_close, "low": day_two_close,
            "close": day_two_close, "volume": 1_000.0,
            "previous_cumulative_volume": 200.0,
        },
    ]
    contexts = {
        (symbol, date(2026, 1, 5)): {
            "previous_open": 11.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": -0.02,
            "previous_closes": [9.0, 9.0, 9.0, 9.0], "previous_ma5": 9.0,
        },
        (symbol, date(2026, 1, 6)): {
            "previous_open": 10.0, "previous_close": 10.1,
            "previous_high": day_one_high, "previous_change_pct": 0.01,
            "previous_closes": [9.0, 9.0, 9.0, 9.0], "previous_ma5": 9.0,
        },
    }
    config = MinutePortfolioConfig(
        symbols=[symbol],
        initial_capital=100_000.0,
        max_positions=1,
        commission_pct=0.0,
        stamp_tax_pct=0.0,
        slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(stop_loss_pct=stop_loss_pct),
        **config_overrides,
    )
    return MinutePortfolioEngine(config).run(rows, contexts)["trades"][0]


@pytest.mark.parametrize(
    ("entry_fill", "expected_time", "expected_price"),
    [
        ("signal_minute_close", "09:30:00", 10.2),
        ("next_minute_open", "09:31:00", 10.3),
    ],
)
def test_engine_uses_configured_minute_entry_fill(
    entry_fill: str,
    expected_time: str,
    expected_price: float,
) -> None:
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
            "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
            "volume": 20_000.0, "previous_cumulative_volume": 200.0,
        },
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": 10.0,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_closes": [9.0, 9.0, 9.0, 9.0],
    }}
    trade = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1, entry_fill=entry_fill,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
    )).run(rows, contexts)["trades"][0]

    assert trade["entry_datetime"].endswith(expected_time)
    assert trade["entry_price"] == expected_price


def test_failed_next_minute_buy_expires_and_a_later_signal_can_retry() -> None:
    symbol = "600000.SH"
    day = date(2026, 1, 5)
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "cumulative_volume": 150.0,
            "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
            "volume": 0.0, "cumulative_volume": 150.0,
            "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 32),
            "open": 10.3, "high": 10.7, "low": 10.2, "close": 10.4,
            "volume": 300.0, "cumulative_volume": 450.0,
            "previous_cumulative_volume": 250.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 33),
            "open": 10.5, "high": 10.6, "low": 10.4, "close": 10.5,
            "volume": 10_000.0, "cumulative_volume": 10_450.0,
            "previous_cumulative_volume": 300.0,
        },
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": 10.0,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_closes": [9.0, 9.0, 9.0, 9.0],
    }}
    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(
            enable_branch_b=False,
            enable_branch_c=False,
        ),
    )).run(rows, contexts)

    assert result["trades"][0]["entry_datetime"].endswith("09:33:00")


def _market_constraint_result(*, execution_bar: dict, previous_close: float) -> dict:
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
            "volume": 100.0, "previous_cumulative_volume": 10_000.0,
            **execution_bar,
        },
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": previous_close,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_closes": [9.0, 9.0, 9.0, 9.0],
    }}
    return MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(
            enable_branch_b=False,
            enable_branch_c=False,
        ),
    )).run(rows, contexts)


def test_engine_rejects_one_price_limit_up_buy() -> None:
    result = _market_constraint_result(
        execution_bar={"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0},
        previous_close=10.0,
    )

    assert result["trades"] == []
    assert result["execution"]["buy_limit_up"] == 1


def test_missing_execution_bar_is_a_suspended_buy() -> None:
    assert _buy_block_reason(
        symbol="600000.SH",
        bar=None,
        previous_close=10.0,
        symbol_name="",
        fill="next_minute_open",
    ) == "buy_suspended"


@pytest.mark.parametrize(
    ("symbol", "name", "limit_price"),
    [
        ("600000.SH", "", 11.0),
        ("600000.SH", "*ST示例", 10.5),
        ("688001.SH", "", 12.0),
        ("300001.SZ", "", 12.0),
        ("830001.BJ", "", 13.0),
    ],
)
def test_buy_limit_check_uses_board_and_st_price_bands(
    symbol: str,
    name: str,
    limit_price: float,
) -> None:
    bar = {
        "open": limit_price,
        "high": limit_price,
        "low": limit_price,
        "close": limit_price,
        "volume": 1_000.0,
    }

    assert _buy_block_reason(
        symbol=symbol,
        bar=bar,
        previous_close=10.0,
        symbol_name=name,
        fill="next_minute_open",
    ) == "buy_limit_up"


def test_engine_keeps_limit_down_exit_pending_until_a_tradable_bar() -> None:
    symbol = "600000.SH"
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": 10.1, "low": 10.0, "close": 10.0,
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 9, 30),
            "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0,
            "volume": 1_000.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 9, 31),
            "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0,
            "volume": 1_000.0, "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 7, 9, 30),
            "open": 9.5, "high": 9.6, "low": 9.4, "close": 9.5,
            "volume": 1_000.0, "previous_cumulative_volume": 100.0,
        },
    ]
    contexts = {
        (symbol, date(2026, 1, 5)): {
            "previous_open": 11.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": -0.02,
            "previous_closes": [9.0, 9.0, 9.0, 9.0],
        },
        (symbol, date(2026, 1, 6)): {
            "previous_open": 10.0, "previous_close": 10.0,
            "previous_high": 10.1, "previous_change_pct": 0.0,
            "previous_closes": [9.0, 9.0, 9.0, 9.0],
        },
        (symbol, date(2026, 1, 7)): {
            "previous_open": 9.0, "previous_close": 9.0,
            "previous_high": 9.0, "previous_change_pct": -0.10,
            "previous_closes": [9.0, 9.0, 9.0, 9.0],
        },
    }
    result = MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(stop_loss_pct=0.05),
    )).run(rows, contexts)

    assert result["trades"][0]["exit_datetime"] == "2026-01-07 09:30:00"
    assert result["execution"]["sell_limit_down"] == 1


@pytest.mark.parametrize(
    ("config_overrides", "day_one_high", "day_two_close", "expected_reason"),
    [
        ({"take_profit_pct": 0.10}, 10.2, 11.1, "take_profit"),
        ({"trailing_stop_pct": 0.10}, 12.0, 10.7, "trailing_stop"),
        (
            {
                "trailing_take_profit_activate_pct": 0.10,
                "trailing_take_profit_drawdown_pct": 0.05,
            },
            12.0,
            11.3,
            "trailing_take_profit",
        ),
        ({"max_hold_days": 1}, 10.2, 10.1, "max_hold_days"),
    ],
)
def test_engine_applies_minute_native_risk_controls(
    config_overrides: dict,
    day_one_high: float,
    day_two_close: float,
    expected_reason: str,
) -> None:
    trade = _risk_control_trade(
        day_one_high=day_one_high,
        day_two_close=day_two_close,
        **config_overrides,
    )

    assert trade["exit_reason"] == expected_reason
    assert trade["exit_datetime"] == "2026-01-06 09:31:00"


def test_zero_stop_loss_disables_the_stop_loss_rule() -> None:
    trade = _risk_control_trade(day_two_close=9.0, stop_loss_pct=0.0)
    assert trade["exit_reason"] == "end_of_backtest"


def test_risk_exit_has_priority_when_ma_exit_triggers_at_the_same_minute() -> None:
    trade = _risk_control_trade(day_two_close=8.0, stop_loss_pct=0.10)
    assert trade["exit_reason"] == "stop_loss"


@pytest.mark.parametrize(
    ("exit_fill", "expected_time"),
    [
        ("signal_minute_close", "2026-01-06 09:30:00"),
        ("next_minute_open", "2026-01-06 09:31:00"),
    ],
)
def test_engine_uses_configured_minute_exit_fill(
    exit_fill: str,
    expected_time: str,
) -> None:
    trade = _risk_control_trade(
        day_two_close=8.0,
        stop_loss_pct=0.10,
        exit_fill=exit_fill,
    )

    assert trade["exit_datetime"] == expected_time


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


def _last_day_signal_result(*, force_close_at_end: bool) -> dict:
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
            "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
        },
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": 10.0,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_closes": [9.0, 9.0, 9.0, 9.0],
    }}
    return MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], start=day, end=day, max_positions=1,
        force_close_at_end=force_close_at_end,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
    )).run(rows, contexts)


def _overnight_position_result(
    *,
    force_close_at_end: bool,
    final_bar: dict | None = None,
    suspend_on_final_day: bool = False,
) -> tuple[dict, MinutePortfolioConfig]:
    symbol = "600000.SH"
    dummy_symbol = "000001.SZ"
    start = date(2026, 1, 5)
    end = date(2026, 1, 6)
    last = final_bar or {"open": 10.7, "high": 10.9, "low": 10.6, "close": 10.8}
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": 10.1, "low": 10.0, "close": 10.0,
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
        },
        ({
            "symbol": dummy_symbol, "datetime": datetime(2026, 1, 6, 15, 0),
            "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
        } if suspend_on_final_day else {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 15, 0),
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
            **last,
        }),
    ]
    contexts = {
        (symbol, start): {
            "previous_open": 11.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": -0.02,
            "previous_closes": [9.0, 9.0, 9.0, 9.0],
        },
        (symbol, end): {
            "previous_open": 10.0, "previous_close": 10.0,
            "previous_high": 10.1, "previous_change_pct": 0.0,
            "previous_closes": [1.0, 1.0, 1.0, 1.0],
        },
    }
    config = MinutePortfolioConfig(
        symbols=[symbol, dummy_symbol] if suspend_on_final_day else [symbol],
        start=start, end=end, initial_capital=100_000.0, max_positions=1,
        force_close_at_end=force_close_at_end,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(stop_loss_pct=0.0),
    )
    return MinutePortfolioEngine(config).run(rows, contexts), config


def test_force_close_skips_new_entries_on_last_trading_day() -> None:
    result = _last_day_signal_result(force_close_at_end=True)

    assert result["trades"] == []
    assert result["open_positions"] == []


def test_disabled_force_close_returns_marked_open_position() -> None:
    result, _ = _overnight_position_result(force_close_at_end=False)

    assert result["trades"] == []
    assert result["open_positions"][0]["mark_price"] == 10.8
    assert result["final_equity"] == pytest.approx(
        result["cash"] + result["open_positions"][0]["market_value"],
    )


def test_force_close_cannot_bypass_final_limit_down() -> None:
    result, _ = _overnight_position_result(
        force_close_at_end=True,
        final_bar={"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0},
    )

    assert result["trades"] == []
    assert result["open_positions"][0]["exit_block_reason"] == "sell_limit_down"


def test_force_close_keeps_a_position_suspended_for_the_final_day() -> None:
    result, _ = _overnight_position_result(
        force_close_at_end=True,
        suspend_on_final_day=True,
    )

    assert result["trades"] == []
    assert result["open_positions"][0]["exit_block_reason"] == "sell_suspended"


def test_open_positions_affect_equity_not_completed_trade_stats() -> None:
    executed, config = _overnight_position_result(force_close_at_end=False)
    stats = MinutePortfolioService._stats(executed, config)

    assert stats["final_equity"] == executed["final_equity"]
    assert stats["n_trades"] == 0
    assert stats["win_rate"] == 0.0


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
                "symbol": ["600000.SH"] * 3,
                "date": [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)],
                "open": [11.0, 10.0, 10.4], "high": [10.5, 10.4, 10.5],
                "close": [10.0, 10.3, 10.4], "ma5": [9.0, 9.1, 9.2],
            })

        def get_minute_range(self, symbols, start, end, asset_type):
            self.minute_start = start
            return __import__("polars").DataFrame({
                "symbol": ["600000.SH"] * 5,
                "datetime": [
                    datetime(2026, 1, 2, 9, 30), datetime(2026, 1, 2, 9, 31),
                    datetime(2026, 1, 5, 9, 30), datetime(2026, 1, 5, 9, 31),
                    datetime(2026, 1, 6, 15, 0),
                ],
                "open": [10.0, 10.0, 10.0, 10.3, 10.4],
                "high": [10.0, 10.0, 10.6, 10.4, 10.5],
                "low": [10.0, 10.0, 10.0, 10.2, 10.2],
                "close": [10.0, 10.0, 10.2, 10.3, 10.4],
                "volume": [100.0, 100.0, 150.0, 100.0, 100.0],
                "amount": [1000.0] * 5,
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


def test_service_applies_stock_names_before_st_filtering() -> None:
    class Repo:
        def get_name_map(self, symbols):
            return {"600000.SH": "*ST 示例"}

        def get_daily_batch(self, symbols, start, end, columns):
            return pl.DataFrame({
                "symbol": ["600000.SH", "600000.SH"],
                "date": [date(2026, 1, 2), date(2026, 1, 5)],
                "open": [11.0, 10.0], "high": [10.5, 10.4],
                "close": [10.0, 10.3], "ma5": [9.0, 9.1],
            })

        def get_minute_range(self, symbols, start, end, asset_type):
            return pl.DataFrame({
                "symbol": ["600000.SH"] * 4,
                "datetime": [
                    datetime(2026, 1, 2, 9, 30), datetime(2026, 1, 2, 9, 31),
                    datetime(2026, 1, 5, 9, 30), datetime(2026, 1, 5, 9, 31),
                ],
                "open": [10.0, 10.0, 10.0, 10.3],
                "high": [10.0, 10.0, 10.6, 10.4],
                "low": [10.0, 10.0, 10.0, 10.2],
                "close": [10.0, 10.0, 10.2, 10.3],
                "volume": [100.0, 100.0, 150.0, 100.0],
                "amount": [1_000.0] * 4,
            })

        def get_index_daily(self, symbol, start, end, columns):
            return pl.DataFrame()

    result = MinutePortfolioService(Repo()).run(MinutePortfolioConfig(
        symbols=["600000.SH"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        basic_filter={"enabled": True, "exclude_st": True},
    ))

    assert result["stats"]["total_trade_count"] == 0


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


def test_service_ignores_preopen_bars_when_triggering_exits() -> None:
    symbol = "600000.SH"

    class Repo:
        def get_daily_batch(self, symbols, start, end, columns):
            return pl.DataFrame({
                "symbol": [symbol, symbol],
                "date": [date(2026, 1, 2), date(2026, 1, 5)],
                "open": [11.0, 10.0],
                "high": [10.5, 10.6],
                "close": [10.0, 10.0],
                "ma5": [9.0, 9.0],
            })

        def get_minute_range(self, symbols, start, end, asset_type):
            return pl.DataFrame({
                "symbol": [symbol] * 7,
                "datetime": [
                    datetime(2026, 1, 2, 9, 30),
                    datetime(2026, 1, 5, 9, 30),
                    datetime(2026, 1, 5, 9, 31),
                    datetime(2026, 1, 6, 9, 16),
                    datetime(2026, 1, 6, 9, 17),
                    datetime(2026, 1, 6, 9, 30),
                    datetime(2026, 1, 6, 9, 31),
                ],
                "open": [10.0, 10.0, 10.0, 9.0, 9.0, 9.0, 8.8],
                "high": [10.0, 10.6, 10.1, 9.1, 9.1, 9.1, 8.9],
                "low": [10.0, 10.0, 9.9, 8.9, 8.9, 8.9, 8.7],
                "close": [10.0, 10.2, 10.0, 9.0, 9.0, 9.0, 8.8],
                "volume": [100.0, 150.0, 1_000.0, 100.0, 100.0, 100.0, 1_000.0],
                "amount": [1_000.0] * 7,
            })

        def get_index_daily(self, symbol, start, end, columns):
            return pl.DataFrame()

    result = MinutePortfolioService(Repo()).run(MinutePortfolioConfig(
        symbols=[symbol],
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        initial_capital=100_000.0,
        max_positions=1,
        commission_pct=0.0,
        stamp_tax_pct=0.0,
        slippage_bps=0.0,
    ))

    assert result["trades"][0]["exit_reason"] == "stop_loss"
    assert result["trades"][0]["exit_datetime"] == "2026-01-06 09:31:00"
