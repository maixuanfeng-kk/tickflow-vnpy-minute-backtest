import pytest

from app.backtest.minute_portfolio import entry_reason, rank_candidates


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
