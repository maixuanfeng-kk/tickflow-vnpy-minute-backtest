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
    assert "4.1.1" in intents[0].diagnostic["matched_conditions"]


def test_sell_trigger_is_strict_and_protection_suppresses_regular_sell():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=3.0, previous_close=3.3, previous_high=3.4, previous_low=2.9,
        closes=(3.0, 3.3),
    )
    moment = datetime(2026, 7, 2, 10, 0)
    strategy.on_minute({"159915.SZ": _bar(moment, 3.2)}, _context(moment, reference))
    assert strategy._strictly_below(3.3, 3.3) is False


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
    assert intents[0].reason == "5.4"


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
    assert [intent.reason for intent in intents] == ["4.1.1"]


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
    assert [intent.reason for intent in intents] == ["4.2.1"]


def test_4232_uses_intraday_gain_from_previous_close_not_today_open():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(
        previous_open=91.1, previous_close=90.0, previous_high=92.0, previous_low=89.0,
        closes=(95.0, 94.0, 92.5, 92.0, 90.0),
    )
    moment = datetime(2026, 7, 2, 9, 30)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(moment, 91.6, open_price=92.0)}, _context(moment, reference),
    )
    assert [intent.reason for intent in intents] == ["4.2.3-2"]


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
        closes=(100.0, 97.0, 94.0, 92.0, 90.0),
    )
    first = datetime(2026, 7, 2, 9, 30)
    strategy.on_minute(
        {"159915.SZ": _bar(first, 89.0, open_price=89.0)}, _context(first, first_reference),
    )

    second_reference = DailyReference(
        previous_open=93.0, previous_close=91.0, previous_high=94.0, previous_low=90.0,
        closes=(100.0, 96.0, 93.0, 92.0, 91.0),
    )
    second = datetime(2026, 7, 3, 9, 30)
    intents = strategy.on_minute(
        {"159915.SZ": _bar(second, 92.5, open_price=92.0)}, _context(second, second_reference),
    )
    assert [intent.reason for intent in intents] == ["4.2.3-1"]


def test_56_requires_breaking_both_previous_lows_when_lowest_is_two_days_old():
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


def test_t_plus_one_position_does_not_emit_a_sell_signal_on_entry_day():
    strategy = Etf159915MinuteStrategy({})
    reference = DailyReference(previous_open=3.5, previous_close=3.4, previous_high=3.6, previous_low=3.2, closes=(3.3, 3.4))
    position = {"159915.SZ": PortfolioPositionView("159915.SZ", 100, 3.5, datetime(2026, 7, 2).date())}
    moment = datetime(2026, 7, 2, 10, 0)
    assert strategy.on_minute(
        {"159915.SZ": _bar(moment, 3.45, open_price=3.5)}, _context(moment, reference, position),
    ) == []
