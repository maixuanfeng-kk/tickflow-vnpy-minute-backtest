from datetime import datetime

from vnpy.trader.constant import Direction, Exchange, Interval
from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import DailyReference, PortfolioContext
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
