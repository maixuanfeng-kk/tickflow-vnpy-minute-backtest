from datetime import date, time

from app.vnpy_backtest.minute_double_ma import (
    buyable_quantity,
    amount_confirmed,
    can_open_new_position,
    can_sell_t_plus_one,
    moving_average_cross,
    round_lot,
)


def test_moving_average_cross_detects_golden_and_death_cross() -> None:
    golden = [10.0] * 20 + [20.0]
    death = [20.0] * 20 + [10.0]

    assert moving_average_cross(golden, 5, 20) == (True, False)
    assert moving_average_cross(death, 5, 20) == (False, True)


def test_moving_average_cross_is_stable_at_a_near_equal_death_cross() -> None:
    closes = [
        11.52, 11.57, 11.59, 11.58, 11.60, 11.59, 11.58,
        11.57, 11.58, 11.57, 11.56, 11.56, 11.56, 11.58,
        11.59, 11.59, 11.58, 11.58, 11.58, 11.57, 11.58,
    ]

    assert moving_average_cross(closes, 5, 20) == (False, True)


def test_amount_confirmation_compares_current_bar_with_previous_window() -> None:
    assert amount_confirmed([100.0] * 20 + [121.0], 20, 1.2)
    assert not amount_confirmed([100.0] * 20 + [119.0], 20, 1.2)


def test_time_cutoff_t_plus_one_and_lot_rounding() -> None:
    assert can_open_new_position(time(14, 30), time(14, 30))
    assert not can_open_new_position(time(14, 31), time(14, 30))
    assert can_sell_t_plus_one(date(2026, 1, 5), date(2026, 1, 6))
    assert not can_sell_t_plus_one(date(2026, 1, 5), date(2026, 1, 5))
    assert round_lot(999, 100) == 900


def test_buyable_quantity_uses_current_cash_and_buy_slippage() -> None:
    assert buyable_quantity(100_000, 11.84, 0.03, 1.0, 100, 0.0001) == 8100
    assert buyable_quantity(99_133.8, 11.80, 0.03, 1.0, 100, 0.0001) == 8100
