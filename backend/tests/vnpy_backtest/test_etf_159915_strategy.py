from datetime import datetime

from vnpy.trader.constant import Direction, Exchange, Interval
from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import DailyReference, PortfolioContext, PortfolioPositionView
from app.vnpy_backtest.strategies.etf_159915_minute import Etf159915MinuteStrategy
from app.vnpy_backtest.strategies.registry import get_strategy


def _bar(
    moment: datetime,
    close: float,
    volume: float = 100.0,
    *,
    open_price: float | None = None,
    high_price: float | None = None,
) -> BarData:
    open_price = close if open_price is None else open_price
    high_price = close if high_price is None else high_price
    return BarData(
        gateway_name="TEST", symbol="159915", exchange=Exchange.SZSE, datetime=moment,
        interval=Interval.MINUTE, volume=volume, turnover=close * volume,
        open_price=open_price, high_price=high_price, low_price=close, close_price=close,
    )


def _context(moment: datetime, reference: DailyReference, positions=None) -> PortfolioContext:
    return PortfolioContext(moment, 100_000.0, 0.0, positions or {}, {"159915.SZ": reference})


def test_strategy_is_registered_as_single_symbol_raw_price_strategy():
    spec = get_strategy("etf_159915_minute")
    assert spec is not None
    assert spec.min_symbols == spec.max_symbols == 1


def test_411_requires_strict_breakout_above_yesterday_high_and_early_high():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=3.0, previous_close=3.2, previous_high=3.5, previous_low=2.9,
        closes=(3.1, 3.2),
    )
    early = datetime(2026, 7, 2, 9, 31)
    moment = datetime(2026, 7, 2, 9, 32)
    strategy.on_minute({"159915.SZ": _bar(early, 3.4)}, _context(early, reference))
    intents = strategy.on_minute({"159915.SZ": _bar(moment, 3.51)}, _context(moment, reference))
    assert intents and intents[0].direction == Direction.LONG
    assert "4_1_1" in intents[0].diagnostic["matched_conditions"]


def test_54_high_open_reversal_returns_one_sell_intent():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(previous_open=3.2, previous_close=3.4, previous_high=3.5, previous_low=3.1, closes=(3.3, 3.4))
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 3.4, datetime(2026, 7, 1).date())}
    first = datetime(2026, 7, 2, 9, 30)
    second = datetime(2026, 7, 2, 9, 31)
    strategy.on_minute({"159915.SZ": _bar(first, 3.5)}, _context(first, reference, position))
    intents = strategy.on_minute({"159915.SZ": _bar(second, 3.45)}, _context(second, reference, position))
    assert len(intents) == 1
    assert intents[0].direction == Direction.SHORT
    assert intents[0].reason == "5_4"


def test_54_sell_can_reenter_above_day_open_after_the_fill():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=9.9,
        previous_close=10.0,
        previous_high=10.1,
        previous_low=9.8,
        closes=(9.9, 10.0),
        opens=(9.8, 9.9),
        lows=(9.7, 9.8),
    )
    position = {
        "159915.SZ": PortfolioPositionView(
            "159915.SZ", 100, 10.0, datetime(2024, 9, 27).date(),
        )
    }
    sell_time = datetime(2024, 9, 30, 9, 40)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(sell_time, 10.19, open_price=10.30)},
        _context(sell_time, reference, position),
    )
    assert [intent.reason for intent in intents] == ["5_4"]

    reentry_time = datetime(2024, 9, 30, 10, 21)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(reentry_time, 10.31)},
        _context(reentry_time, reference),
    )
    assert [intent.reason for intent in intents] == ["5_4"]


def test_1500_bar_does_not_create_a_signal_without_a_next_bar():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(previous_open=3.2, previous_close=3.4, previous_high=3.5, previous_low=3.1, closes=(3.3, 3.4))
    moment = datetime(2026, 7, 2, 15, 0)
    assert strategy.on_minute({"159915.SZ": _bar(moment, 3.2)}, _context(moment, reference)) == []


def test_411_uses_c2_as_the_high_and_close_gain_base():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=109.0, previous_close=110.0, previous_high=110.4, previous_low=108.0,
        closes=(100.0, 110.0),
    )
    early = datetime(2026, 7, 2, 9, 31)
    moment = datetime(2026, 7, 2, 9, 32)
    strategy.on_minute({"159915.SZ": _bar(early, 110.0)}, _context(early, reference))
    intents = strategy.on_minute({"159915.SZ": _bar(moment, 110.5)}, _context(moment, reference))
    assert [intent.reason for intent in intents] == ["4_1_1"]


def test_421_uses_c2_as_the_open_and_close_gain_base():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=90.6, previous_close=90.0, previous_high=91.0, previous_low=89.0,
        closes=(100.0, 90.0),
    )
    early = datetime(2026, 7, 2, 9, 31)
    moment = datetime(2026, 7, 2, 9, 32)
    strategy.on_minute({"159915.SZ": _bar(early, 90.8)}, _context(early, reference))
    intents = strategy.on_minute({"159915.SZ": _bar(moment, 91.0)}, _context(moment, reference))
    assert [intent.reason for intent in intents] == ["4_2_1"]


def test_4232_uses_intraday_gain_from_previous_close_not_today_open():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=91.1, previous_close=90.0, previous_high=92.0, previous_low=89.0,
        closes=(100.0, 95.0, 94.0, 92.5, 92.0, 90.0),
    )
    moment = datetime(2026, 7, 2, 9, 31)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 91.6, open_price=92.0)}, _context(moment, reference),
    )
    assert [intent.reason for intent in intents] == ["4_2_3_2"]


def test_doji_day_does_not_enter_the_42_buy_rules():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=99.0, previous_close=99.0, previous_high=100.0, previous_low=98.0,
        closes=(100.0, 99.0),
    )
    early = datetime(2026, 7, 2, 9, 31)
    moment = datetime(2026, 7, 2, 9, 32)
    strategy.on_minute({"159915.SZ": _bar(early, 100.0)}, _context(early, reference))
    assert strategy.on_minute({"159915.SZ": _bar(moment, 100.1)}, _context(moment, reference)) == []


def test_4231_remains_active_when_a_new_lower_priority_4232_state_appears():
    strategy = Etf159915MinuteStrategy({})
    first_reference = DailyReference(
        previous_open=91.0, previous_close=90.0, previous_high=92.0, previous_low=89.0,
        closes=(100.0, 100.0, 97.0, 94.0, 92.0, 90.0),
    )
    first = datetime(2026, 7, 2, 9, 31)
    strategy.on_minute(
        {"159915.SZ": _bar(first, 89.0, open_price=89.0)}, _context(first, first_reference),
    )

    second_reference = DailyReference(
        previous_open=93.0, previous_close=91.0, previous_high=94.0, previous_low=90.0,
        closes=(100.0, 100.0, 96.0, 93.0, 92.0, 91.0),
    )
    second = datetime(2026, 7, 3, 9, 31)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(second, 92.5, open_price=92.0)}, _context(second, second_reference),
    )
    assert [intent.reason for intent in intents] == ["4_2_3_1_before"]


def test_4232_carries_to_next_day_and_suppresses_411_until_its_trigger_breaks():
    strategy = Etf159915MinuteStrategy({})
    setup_reference = DailyReference(
        previous_open=1.967,
        previous_close=1.938,
        previous_high=1.983,
        previous_low=1.938,
        opens=(1.978, 1.965, 1.975, 1.956, 1.967, 1.967),
        closes=(1.978, 1.976, 1.990, 1.968, 1.971, 1.938),
        lows=(1.970, 1.962, 1.951, 1.925, 1.954, 1.938),
    )
    setup_day = datetime(2025, 1, 13, 14, 59)
    assert strategy.on_minute(
        {"159915.SZ": _bar(setup_day, 1.942, open_price=1.916)},
        _context(setup_day, setup_reference),
    ) == []

    carried_reference = DailyReference(
        previous_open=1.916,
        previous_close=1.942,
        previous_high=1.959,
        previous_low=1.916,
        opens=(1.965, 1.975, 1.956, 1.967, 1.967, 1.916),
        closes=(1.976, 1.990, 1.968, 1.971, 1.938, 1.942),
        lows=(1.962, 1.951, 1.925, 1.954, 1.938, 1.916),
    )
    early = datetime(2025, 1, 14, 9, 31)
    ordinary_breakout = datetime(2025, 1, 14, 9, 52)
    special_breakout = datetime(2025, 1, 14, 9, 56)
    strategy.on_minute(
        {"159915.SZ": _bar(early, 1.944, high_price=1.953)},
        _context(early, carried_reference),
    )
    assert strategy.on_minute(
        {"159915.SZ": _bar(ordinary_breakout, 1.962)},
        _context(ordinary_breakout, carried_reference),
    ) == []
    intents = strategy.on_minute(
        {"159915.SZ": _bar(special_breakout, 1.972)},
        _context(special_breakout, carried_reference),
    )
    assert [intent.reason for intent in intents] == ["4_2_3_2_before"]


def test_56_does_not_sell_when_price_breaks_only_the_higher_previous_low():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0, previous_close=10.05, previous_high=10.2, previous_low=10.0,
        closes=(10.1, 10.05), lows=(9.5, 10.0),
    )
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 10.0, datetime(2026, 7, 1).date())}
    moment = datetime(2026, 7, 2, 10, 0)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 9.99)}, _context(moment, reference, position),
    ) == []


def test_56_sells_when_price_breaks_both_previous_lows():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0, previous_close=10.05, previous_high=10.2, previous_low=10.0,
        closes=(10.1, 10.05), lows=(9.5, 10.0),
    )
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 10.0, datetime(2026, 7, 1).date())}
    moment = datetime(2026, 7, 2, 10, 0)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 9.49)}, _context(moment, reference, position),
    )
    assert [intent.reason for intent in intents] == ["5_6"]


def test_t_plus_one_position_does_not_emit_a_sell_signal_on_entry_day():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(previous_open=3.5, previous_close=3.4, previous_high=3.6, previous_low=3.2, closes=(3.3, 3.4))
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 3.5, datetime(2026, 7, 2).date())}
    moment = datetime(2026, 7, 2, 10, 0)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 3.45, open_price=3.5)}, _context(moment, reference, position),
    ) == []


def test_protection_period_suppresses_56_until_its_own_trigger_breaks():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0, previous_close=10.5, previous_high=10.6, previous_low=9.0,
        closes=(10.0, 10.5), opens=(10.0, 10.0), lows=(9.5, 9.0),
    )
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 10.0, datetime(2026, 7, 1).date())}
    moment = datetime(2026, 7, 2, 10, 0)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 10.35, open_price=10.4, high_price=10.4)},
        _context(moment, reference, position),
    ) == []


def test_fresh_53_uses_the_latest_consecutive_strong_entity_days():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=2.880, previous_close=2.927, previous_high=2.954, previous_low=2.870,
        opens=(2.680, 2.799, 2.880), closes=(2.806, 2.865, 2.927), lows=(2.677, 2.780, 2.870),
    )
    old_position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 2.9, datetime(2025, 9, 1).date())}
    sell_time = datetime(2025, 9, 2, 11, 2)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(sell_time, 2.80)}, _context(sell_time, reference, old_position),
    )
    assert [intent.reason for intent in intents] == ["5_3"]


def test_fresh_52_uses_previous_day_low_and_log_reason():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0,
        previous_close=10.2,
        previous_high=10.3,
        previous_low=9.8,
        opens=(9.0, 10.0),
        closes=(9.0, 10.2),
        lows=(8.8, 9.8),
    )
    position = {
        "159915.SZ": PortfolioPositionView(
            "159915.SZ", 100, 10.0, datetime(2026, 7, 1).date(),
        )
    }
    moment = datetime(2026, 7, 2, 10, 0)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 9.80)},
        _context(moment, reference, position),
    )
    assert [intent.reason for intent in intents] == ["5_2"]


def test_consumed_53_setup_does_not_sell_a_later_position():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=2.880,
        previous_close=2.927,
        previous_high=2.954,
        previous_low=2.870,
        opens=(2.680, 2.799, 2.880),
        closes=(2.806, 2.865, 2.927),
        lows=(2.677, 2.780, 2.870),
    )
    old_position = {
        "159915.SZ": PortfolioPositionView(
            "159915.SZ", 100, 2.9, datetime(2025, 9, 1).date(),
        )
    }
    sell_time = datetime(2025, 9, 2, 11, 2)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(sell_time, 2.80)},
        _context(sell_time, reference, old_position),
    )
    assert [intent.reason for intent in intents] == ["5_3"]

    fill_time = datetime(2025, 9, 2, 11, 3)
    strategy.on_minute(
        {"159915.SZ": _bar(fill_time, 2.80)},
        _context(fill_time, reference),
    )

    later_position = {
        "159915.SZ": PortfolioPositionView(
            "159915.SZ", 100, 2.8, datetime(2025, 9, 4).date(),
        )
    }
    later_reference = DailyReference(
        previous_open=2.80,
        previous_close=2.82,
        previous_high=2.85,
        previous_low=2.75,
        opens=(2.680, 2.799, 2.880, 2.85, 2.80),
        closes=(2.806, 2.865, 2.927, 2.80, 2.82),
        lows=(2.677, 2.780, 2.870, 2.75, 2.75),
    )
    later_time = datetime(2025, 9, 5, 9, 31)
    assert strategy.on_minute(
        {"159915.SZ": _bar(later_time, 2.81)},
        _context(later_time, later_reference, later_position),
    ) == []


def test_tail_3_rejects_when_today_intraday_gain_reaches_two_percent():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=100.0, previous_close=99.5, previous_high=100.5, previous_low=99.0,
        closes=(100.0, 99.5),
    )
    moment = datetime(2026, 7, 2, 14, 46)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 102.1, open_price=100.0, high_price=102.1)},
        _context(moment, reference),
    ) == []


def test_tail_3_rejects_when_today_intraday_gain_equals_two_percent():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=100.0, previous_close=99.5, previous_high=100.5, previous_low=99.0,
        closes=(100.0, 99.5),
    )
    moment = datetime(2026, 7, 2, 14, 46)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 101.49, open_price=100.0, high_price=101.49)},
        _context(moment, reference),
    ) == []


def test_tail_3_compares_today_entity_gain_with_yesterday_entity_gain():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=100.6,
        previous_close=101.0,
        previous_high=101.1,
        previous_low=100.5,
        closes=(100.0, 101.0),
    )
    moment = datetime(2026, 7, 2, 14, 46)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 101.606, open_price=101.0, high_price=101.606)},
        _context(moment, reference),
    )
    assert [intent.reason for intent in intents] == ["4_3_3"]


def test_tail_buy_starts_at_1446_to_match_trade_log_timestamps():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0, previous_close=9.8, previous_high=10.1, previous_low=9.7,
        closes=(10.0, 9.8),
    )
    before_window = datetime(2026, 7, 2, 14, 45)
    assert strategy.on_minute(
        {"159915.SZ": _bar(before_window, 10.0, open_price=9.8)},
        _context(before_window, reference),
    ) == []

    in_window = datetime(2026, 7, 2, 14, 46)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(in_window, 10.0)},
        _context(in_window, reference),
    )
    assert [intent.reason for intent in intents] == ["4_3_2"]


def test_423_special_state_uses_five_day_average_only():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=80.0, previous_close=75.0, previous_high=95.0, previous_low=74.0,
        closes=(50.0, 50.0, 100.0, 100.0, 100.0, 80.0, 75.0),
    )
    moment = datetime(2026, 7, 2, 9, 31)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 95.1, open_price=92.5)}, _context(moment, reference),
    )
    assert [intent.reason for intent in intents] == ["4_2_3_1"]


def test_423_special_state_does_not_require_a_bearish_entity_day():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=89.0, previous_close=90.0, previous_high=95.0, previous_low=88.0,
        closes=(50.0, 100.0, 100.0, 100.0, 100.0, 95.0, 90.0),
    )
    moment = datetime(2026, 7, 2, 9, 31)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 95.1, open_price=92.5)}, _context(moment, reference),
    )
    assert [intent.reason for intent in intents] == ["4_2_3_1"]


def test_423_special_state_requires_enough_history_for_two_complete_five_day_averages():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=80.0, previous_close=75.0, previous_high=95.0, previous_low=74.0,
        closes=(100.0, 100.0, 100.0, 80.0, 75.0),
    )
    moment = datetime(2026, 7, 2, 9, 31)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 95.1, open_price=92.5)}, _context(moment, reference),
    ) == []


def test_423_requires_previous_close_to_be_more_than_1_5_percent_below_ma5():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=99.0,
        previous_close=98.6,
        previous_high=100.0,
        previous_low=98.0,
        closes=(100.6, 100.4, 100.4, 100.4, 100.2, 98.6),
    )
    moment = datetime(2026, 7, 2, 9, 31)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 99.2, open_price=99.0)},
        _context(moment, reference),
    ) == []


def test_423_does_not_trigger_when_previous_close_is_exactly_1_5_percent_below_ma5():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=99.0,
        previous_close=98.5,
        previous_high=101.0,
        previous_low=98.0,
        closes=(110.0, 100.0, 100.0, 100.0, 101.5, 98.5),
    )
    moment = datetime(2026, 7, 2, 9, 31)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 102.0, open_price=99.0)},
        _context(moment, reference),
    ) == []


def test_sell_window_ends_at_1457():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0, previous_close=10.05, previous_high=10.2, previous_low=10.0,
        closes=(10.1, 10.05), lows=(9.5, 10.0),
    )
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 10.0, datetime(2026, 7, 1).date())}
    moment = datetime(2026, 7, 2, 14, 58)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 9.8)}, _context(moment, reference, position),
    ) == []


def test_sell_window_starts_at_0931():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0, previous_close=10.0, previous_high=10.1, previous_low=9.9,
        closes=(10.0, 10.0), lows=(9.8, 9.9),
    )
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 10.0, datetime(2026, 7, 1).date())}
    moment = datetime(2026, 7, 2, 9, 30)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 9.7)}, _context(moment, reference, position),
    ) == []


def test_56_sells_for_a_strict_one_percent_drop_from_yesterday_close():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0, previous_close=10.04, previous_high=10.2, previous_low=9.0,
        closes=(10.0, 10.04), lows=(8.9, 9.0),
    )
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 10.0, datetime(2026, 7, 1).date())}
    moment = datetime(2026, 7, 2, 10, 0)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 9.92)}, _context(moment, reference, position),
    )
    assert [intent.reason for intent in intents] == ["5_6"]


def test_52_uses_a2_strong_day_low_as_the_sell_trigger():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0, previous_close=10.0, previous_high=10.1, previous_low=9.8,
        opens=(9.0, 9.0, 10.0), closes=(9.0, 10.0, 10.0), lows=(8.8, 9.5, 9.8),
    )
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 10.0, datetime(2026, 7, 1).date())}
    moment = datetime(2026, 7, 3, 10, 0)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 9.49)}, _context(moment, reference, position),
    )
    assert [intent.reason for intent in intents] == ["5_2_before_2_2"]


def test_previous_52_suppresses_56_and_uses_before_reason_when_trigger_breaks():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0,
        previous_close=10.0,
        previous_high=10.1,
        previous_low=10.0,
        opens=(9.0, 9.0, 10.0),
        closes=(9.0, 10.0, 10.0),
        lows=(8.8, 9.0, 10.0),
    )
    position = {
        "159915.SZ": PortfolioPositionView(
            "159915.SZ", 100, 10.0, datetime(2025, 1, 14).date(),
        )
    }
    protected = datetime(2025, 1, 22, 9, 55)
    assert strategy.on_minute(
        {"159915.SZ": _bar(protected, 9.50)},
        _context(protected, reference, position),
    ) == []

    broken = datetime(2025, 1, 22, 9, 56)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(broken, 9.00)},
        _context(broken, reference, position),
    )
    assert [intent.reason for intent in intents] == ["5_2_before_2_2"]


def test_previous_52_is_not_replaced_by_a_fresh_52_setup():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=10.0,
        previous_close=10.2,
        previous_high=10.3,
        previous_low=9.8,
        opens=(9.0, 10.0, 10.0),
        closes=(9.0, 10.0, 10.2),
        lows=(8.8, 9.0, 9.8),
    )
    position = {
        "159915.SZ": PortfolioPositionView(
            "159915.SZ", 100, 10.0, datetime(2026, 7, 1).date(),
        )
    }
    protected_by_older_setup = datetime(2026, 7, 3, 10, 0)
    assert strategy.on_minute(
        {"159915.SZ": _bar(protected_by_older_setup, 9.50)},
        _context(protected_by_older_setup, reference, position),
    ) == []
