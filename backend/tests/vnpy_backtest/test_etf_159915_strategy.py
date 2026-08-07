from datetime import datetime

from vnpy.trader.constant import Direction, Exchange, Interval
from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import DailyReference, PortfolioContext, PortfolioPositionView
from app.vnpy_backtest.strategies.etf_159915_minute import Etf159915MinuteStrategy
from app.vnpy_backtest.strategies.registry import get_strategy


def _bar(moment: datetime, close: float, volume: float = 100.0) -> BarData:
    return BarData(
        gateway_name="TEST", symbol="159915", exchange=Exchange.SZSE, datetime=moment,
        interval=Interval.MINUTE, volume=volume, turnover=close * volume,
        open_price=close, high_price=close, low_price=close, close_price=close,
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
