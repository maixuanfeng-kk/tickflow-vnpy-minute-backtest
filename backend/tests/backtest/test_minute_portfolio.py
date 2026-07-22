from datetime import date, datetime, timedelta

import pytest
import polars as pl

from app.backtest.minute_portfolio import (
    LocalMinuteParquetRepository,
    MinutePortfolioConfig,
    MinutePortfolioEngine,
    MinutePortfolioService,
    OpeningVolumeStrategyParams,
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
    assert trade["pnl_amount"] == pytest.approx(trade["pnl_pct"] * trade["entry_cost"], abs=0.1)
    assert trade["duration"] == 1
    assert [row["date"] for row in result["equity_curve"]] == ["2026-01-05", "2026-01-06"]
    assert result["equity_curve"][-1]["value"] == pytest.approx(result["cash"], abs=0.01)
    assert result["drawdown_curve"][-1]["value"] <= 0


def test_service_reads_daily_and_minute_rows_and_returns_backtest_shape() -> None:
    class Repo:
        minute_start = None

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

    repo = Repo()
    result = MinutePortfolioService(repo).run(MinutePortfolioConfig(
        symbols=["600000.SH"],
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_capital=2_000_000.0,
        max_positions=4,
    ))

    assert result["config"]["engine"] == "minute_portfolio"
    assert result["config"]["symbols"] == ["600000.SH"]
    assert result["config"]["initial_capital"] == 2_000_000.0
    assert result["config"]["max_positions"] == 4
    assert result["stats"]["total_trade_count"] == 1
    assert repo.minute_start <= date(2026, 1, 2)


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

        def run(self, rows, contexts):
            captured["dates"] = {row["datetime"].date() for row in rows}
            return {"cash": 1_000_000.0, "trades": []}

    monkeypatch.setattr("app.backtest.minute_portfolio.MinutePortfolioEngine", Engine)
    MinutePortfolioService(Repo()).run(MinutePortfolioConfig(
        symbols=["600000.SH"], start=date(2026, 1, 5), end=date(2026, 1, 5),
    ))

    assert captured["dates"] == {date(2026, 1, 5)}
