from datetime import date, datetime, timedelta

import pytest

from app.backtest.minute_portfolio import (
    MinutePortfolioConfig,
    MinutePortfolioEngine,
    MinutePortfolioService,
    entry_reason,
    rank_candidates,
)


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
    assert entry_reason(
        previous_open=10.0,
        previous_close=10.0,
        previous_change_pct=0.04,
        today_return=0.05,
        volume_ratio=1.5,
        crossed_previous_high=False,
    ) is None


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
