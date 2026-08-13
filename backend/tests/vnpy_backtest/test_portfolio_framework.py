from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

from vnpy.trader.constant import Direction, Exchange, Interval
from vnpy.trader.object import BarData

from app.vnpy_backtest.market_rules import price_limit_bounds, rule_for_symbol
from app.vnpy_backtest.portfolio import DailyContextBuilder, MultiSymbolNextBarOpenEngine, PortfolioFill
from app.vnpy_backtest.service import VnpyMinuteBacktestService
from app.vnpy_backtest.strategies.base import DailyReference, OrderIntent, PortfolioContext, PortfolioPositionView
from app.vnpy_backtest.strategies.opening_breakout_condition_1 import OpeningBreakoutCondition1Strategy
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy
from app.vnpy_backtest.strategies.registry import get_strategy, list_strategies
from app.tickflow.repository import KlineRepository


def _bar(symbol: str, exchange: Exchange, moment: datetime, price: float = 10.0) -> BarData:
    return BarData(
        gateway_name="TEST", symbol=symbol.split(".")[0], exchange=exchange,
        datetime=moment, interval=Interval.MINUTE, volume=10_000, turnover=100_000,
        open_price=price, high_price=price, low_price=price, close_price=price,
    )


class _BuyThenSell:
    def on_minute(self, bars, context):
        if context.timestamp.time().minute == 30 and not context.positions:
            return [OrderIntent(symbol, Direction.LONG, "test_buy") for symbol in bars]
        if context.timestamp.date().day == 6 and context.timestamp.time().minute == 30:
            return [OrderIntent(symbol, Direction.SHORT, "test_sell") for symbol in context.positions]
        return []


def test_registry_exposes_only_portfolio_strategies() -> None:
    portfolio_spec = get_strategy("opening_breakout_pool")
    assert portfolio_spec is not None
    assert portfolio_spec.min_symbols == 1
    assert portfolio_spec.max_symbols == 1000
    assert get_strategy("opening_breakout_condition_1") is not None
    assert get_strategy("opening_breakout_condition_2") is not None
    assert get_strategy("opening_breakout_condition_3") is not None
    assert get_strategy("minute_double_ma_volume") is None
    assert [item.id for item in list_strategies()] == [
        "etf_159915_minute",
        "opening_breakout_pool",
        "opening_breakout_condition_1",
        "opening_breakout_condition_2",
        "opening_breakout_condition_3",
    ]


def test_all_a_board_rules_cover_star_and_bse() -> None:
    assert rule_for_symbol("600000.SH").first_buy_minimum == 100
    assert rule_for_symbol("300001.SZ").price_limit_pct == 0.20
    assert rule_for_symbol("688001.SH").first_buy_minimum == 200
    assert rule_for_symbol("920000.BJ").price_limit_pct == 0.30
    assert rule_for_symbol("600000.SH", limit_pct=0.10).price_limit_pct == 0.10
    assert rule_for_symbol(
        "600000.SH", name="*ST sample", trading_day=date(2026, 5, 22),
    ).price_limit_pct == 0.05
    assert rule_for_symbol(
        "600000.SH", name="*ST sample", trading_day=date(2026, 7, 6),
    ).price_limit_pct == 0.10
    assert rule_for_symbol(
        "300001.SZ", name="*ST sample", trading_day=date(2026, 5, 22),
    ).price_limit_pct == 0.20
    assert rule_for_symbol("600000.SH", name="*ST sample", limit_pct=0.10).price_limit_pct == 0.10
    assert price_limit_bounds(94.48, rule_for_symbol("002938.SZ")) == (103.93, 85.03)


def test_portfolio_engine_equal_buys_and_t_plus_one_sell() -> None:
    start = datetime(2026, 1, 5, 9, 30)
    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=10_000, commission_rate=0, stamp_tax_rate=0,
        slippage_rate=0, min_commission=0, max_volume_ratio=0.10, reserve_ratio=0.03,
        max_positions=2,
    )
    strategy = _BuyThenSell()
    first_day = {
        "600000.SH": [_bar("600000.SH", Exchange.SSE, start), _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=1))],
        "300001.SZ": [_bar("300001.SZ", Exchange.SZSE, start), _bar("300001.SZ", Exchange.SZSE, start + timedelta(minutes=1))],
    }
    engine.run_day(first_day, strategy, {})
    assert [fill.volume for fill in engine.fills] == [400, 400]
    assert engine.cash == 2_000

    second_start = datetime(2026, 1, 6, 9, 30)
    second_day = {
        "600000.SH": [_bar("600000.SH", Exchange.SSE, second_start), _bar("600000.SH", Exchange.SSE, second_start + timedelta(minutes=1))],
        "300001.SZ": [_bar("300001.SZ", Exchange.SZSE, second_start), _bar("300001.SZ", Exchange.SZSE, second_start + timedelta(minutes=1))],
    }
    engine.run_day(second_day, strategy, {})
    assert [fill.direction for fill in engine.fills] == [Direction.LONG, Direction.LONG, Direction.SHORT, Direction.SHORT]
    assert engine.cash == 10_000


def test_portfolio_equal_sizing_is_strict_and_score_sizing_is_explicit() -> None:
    start = datetime(2026, 1, 5, 9, 30)
    bars = {
        "600000.SH": [_bar("600000.SH", Exchange.SSE, start), _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=1))],
        "300001.SZ": [_bar("300001.SZ", Exchange.SZSE, start), _bar("300001.SZ", Exchange.SZSE, start + timedelta(minutes=1))],
    }

    class _WeightedSignals:
        def on_minute(self, _bars, context):
            if context.timestamp != start:
                return []
            return [
                OrderIntent("600000.SH", Direction.LONG, "entry", target_cash=9_999,
                            diagnostic={"matched_conditions": ["condition 1"]}),
                OrderIntent("300001.SZ", Direction.LONG, "entry", target_cash=1,
                            diagnostic={"matched_conditions": ["condition 1", "condition 2"]}),
            ]

    equal = MultiSymbolNextBarOpenEngine(
        initial_cash=100_000, commission_rate=0, stamp_tax_rate=0, slippage_rate=0,
        min_commission=0, max_volume_ratio=None, reserve_ratio=0, position_sizing="equal",
        max_positions=2,
    )
    equal.run_day(bars, _WeightedSignals(), {})
    assert [fill.volume for fill in equal.fills] == [5_000, 5_000]

    score_weighted = MultiSymbolNextBarOpenEngine(
        initial_cash=100_000, commission_rate=0, stamp_tax_rate=0, slippage_rate=0,
        min_commission=0, max_volume_ratio=None, reserve_ratio=0, position_sizing="score_weight",
    )
    score_weighted.run_day(bars, _WeightedSignals(), {})
    assert {fill.symbol: fill.volume for fill in score_weighted.fills} == {"600000.SH": 3_300, "300001.SZ": 6_600}


def test_portfolio_equal_sizing_reserves_cash_for_unfilled_position_slots() -> None:
    start = datetime(2026, 1, 5, 9, 30)

    class _SequentialSignals:
        def on_minute(self, bars, context):
            if context.timestamp == start:
                return [OrderIntent("600000.SH", Direction.LONG, "first")]
            if context.timestamp == start + timedelta(minutes=1):
                return [OrderIntent("300001.SZ", Direction.LONG, "second")]
            return []

    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=80_000, commission_rate=0, stamp_tax_rate=0, slippage_rate=0,
        min_commission=0, max_volume_ratio=None, reserve_ratio=0,
        max_positions=8, position_sizing="equal",
    )
    bars = {
        "600000.SH": [
            _bar("600000.SH", Exchange.SSE, start),
            _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=1)),
            _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=2)),
        ],
        "300001.SZ": [
            _bar("300001.SZ", Exchange.SZSE, start),
            _bar("300001.SZ", Exchange.SZSE, start + timedelta(minutes=1)),
            _bar("300001.SZ", Exchange.SZSE, start + timedelta(minutes=2)),
        ],
    }
    engine.run_day(bars, _SequentialSignals(), {})

    assert [(fill.symbol, fill.volume) for fill in engine.fills] == [
        ("600000.SH", 1_000),
        ("300001.SZ", 1_000),
    ]
    assert engine.cash == 60_000


def test_portfolio_equal_sizing_refreshes_after_full_exit_with_dynamic_reserve() -> None:
    first_day_start = datetime(2026, 1, 5, 9, 30)
    second_day_start = datetime(2026, 1, 6, 9, 30)

    class _ExitThenReenter:
        def on_minute(self, _bars, context):
            if context.timestamp == first_day_start:
                return [OrderIntent("600000.SH", Direction.LONG, "day_one_entry")]
            if context.timestamp == second_day_start:
                return [OrderIntent("600000.SH", Direction.SHORT, "full_exit")]
            if context.timestamp == second_day_start + timedelta(minutes=1) and not context.positions:
                return [OrderIntent("300001.SZ", Direction.LONG, "reenter_after_exit")]
            return []

    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=100_000, commission_rate=0, stamp_tax_rate=0, slippage_rate=0,
        min_commission=0, max_volume_ratio=None, reserve_ratio=0.03,
        max_positions=8, position_sizing="equal",
    )
    first_day = {
        "600000.SH": [
            _bar("600000.SH", Exchange.SSE, first_day_start, 10),
            _bar("600000.SH", Exchange.SSE, first_day_start + timedelta(minutes=1), 10),
        ],
    }
    second_day = {
        "600000.SH": [
            _bar("600000.SH", Exchange.SSE, second_day_start, 20),
            _bar("600000.SH", Exchange.SSE, second_day_start + timedelta(minutes=1), 20),
            _bar("600000.SH", Exchange.SSE, second_day_start + timedelta(minutes=2), 20),
        ],
        "300001.SZ": [
            _bar("300001.SZ", Exchange.SZSE, second_day_start, 10),
            _bar("300001.SZ", Exchange.SZSE, second_day_start + timedelta(minutes=1), 10),
            _bar("300001.SZ", Exchange.SZSE, second_day_start + timedelta(minutes=2), 10),
        ],
    }
    strategy = _ExitThenReenter()
    engine.run_day(first_day, strategy, {})
    assert [(fill.symbol, fill.volume) for fill in engine.fills] == [("600000.SH", 1_200)]

    engine.run_day(second_day, strategy, {})
    assert [(fill.symbol, fill.direction, fill.volume) for fill in engine.fills] == [
        ("600000.SH", Direction.LONG, 1_200),
        ("600000.SH", Direction.SHORT, 1_200),
        ("300001.SZ", Direction.LONG, 1_300),
    ]


def test_portfolio_equal_sizing_uses_current_cash_reserve_after_a_sale() -> None:
    first_day_start = datetime(2026, 1, 5, 9, 30)
    second_day_start = datetime(2026, 1, 6, 9, 30)

    class _ExitThenReenter:
        def on_minute(self, _bars, context):
            if context.timestamp == first_day_start:
                return [OrderIntent("600000.SH", Direction.LONG, "day_one_entry")]
            if context.timestamp == second_day_start:
                return [OrderIntent("600000.SH", Direction.SHORT, "full_exit")]
            if context.timestamp == second_day_start + timedelta(minutes=1) and not context.positions:
                return [OrderIntent("300001.SZ", Direction.LONG, "reenter_after_exit")]
            return []

    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=100_000, commission_rate=0, stamp_tax_rate=0, slippage_rate=0,
        min_commission=0, max_volume_ratio=None, reserve_ratio=0.03,
        max_positions=1, position_sizing="equal",
    )
    engine.run_day({
        "600000.SH": [_bar("600000.SH", Exchange.SSE, first_day_start, 10), _bar("600000.SH", Exchange.SSE, first_day_start + timedelta(minutes=1), 10)],
    }, _ExitThenReenter(), {})
    engine.run_day({
        "600000.SH": [
            _bar("600000.SH", Exchange.SSE, second_day_start, 20),
            _bar("600000.SH", Exchange.SSE, second_day_start + timedelta(minutes=1), 20),
            _bar("600000.SH", Exchange.SSE, second_day_start + timedelta(minutes=2), 20),
        ],
        "300001.SZ": [
            _bar("300001.SZ", Exchange.SZSE, second_day_start, 10),
            _bar("300001.SZ", Exchange.SZSE, second_day_start + timedelta(minutes=1), 10),
            _bar("300001.SZ", Exchange.SZSE, second_day_start + timedelta(minutes=2), 10),
        ],
    }, _ExitThenReenter(), {})

    # Cash after the 9,700-share exit at 20 is 197,000.  The next entry uses
    # 97% of that current balance, rounded down to a 100-share lot.
    assert [(fill.symbol, fill.volume) for fill in engine.fills] == [
        ("600000.SH", 9_700), ("600000.SH", 9_700), ("300001.SZ", 19_100),
    ]
    assert engine.cash == 6_000


def test_buy_budget_excludes_commission_from_the_97_percent_security_amount() -> None:
    start = datetime(2026, 1, 5, 9, 30)

    class _EntryOnly:
        def on_minute(self, _bars, context):
            if context.timestamp == start:
                return [OrderIntent("159915.SZ", Direction.LONG, "entry")]
            return []

    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=100_000, commission_rate=0.00012, stamp_tax_rate=0,
        slippage_rate=0, min_commission=5, max_volume_ratio=None,
        reserve_ratio=0.03, max_positions=1, commission_outside_budget=True,
    )
    bars = {
        "159915.SZ": [_bar("159915.SZ", Exchange.SZSE, start, 10), _bar("159915.SZ", Exchange.SZSE, start + timedelta(minutes=1), 10)],
    }
    engine.run_day(bars, _EntryOnly(), {})

    assert engine.fills[0].volume == 9_700
    assert engine.fills[0].price * engine.fills[0].volume == 97_000
    assert engine.fills[0].commission == 11.64
    assert engine.fills[0].entry_position_pct == 0.97
    assert round(engine.cash, 2) == 2_988.36


def test_portfolio_tied_priority_uses_symbol_order_despite_input_order() -> None:
    start = datetime(2026, 1, 5, 9, 30)

    class _TiedSignals:
        def on_minute(self, bars, context):
            if context.timestamp != start:
                return []
            return [OrderIntent(symbol, Direction.LONG, "entry", diagnostic={"matched_conditions": ["condition 1"]}) for symbol in bars]

    original = {
        "600000.SH": [_bar("600000.SH", Exchange.SSE, start), _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=1))],
        "300001.SZ": [_bar("300001.SZ", Exchange.SZSE, start), _bar("300001.SZ", Exchange.SZSE, start + timedelta(minutes=1))],
    }
    reversed_bars = dict(reversed(list(original.items())))
    results = []
    for bars in (original, reversed_bars):
        engine = MultiSymbolNextBarOpenEngine(
            initial_cash=100_000, commission_rate=0, stamp_tax_rate=0, slippage_rate=0,
            min_commission=0, max_volume_ratio=None, reserve_ratio=0, max_positions=1,
        )
        engine.run_day(bars, _TiedSignals(), {})
        results.append([fill.symbol for fill in engine.fills])
    assert results == [["300001.SZ"], ["300001.SZ"]]


def test_daily_context_uses_completed_days_only() -> None:
    start = datetime(2026, 1, 5, 9, 30)
    builder = DailyContextBuilder()
    builder.add_day({"600000.SH": [_bar("600000.SH", Exchange.SSE, start, 10), _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=1), 11)]})
    reference = builder.references()["600000.SH"]
    assert reference.previous_close == 11
    assert reference.previous_high == 11
    assert reference.closes == (11,)
    assert reference.previous_cumulative_volumes[start.time()] == 10_000


def test_daily_context_uses_official_daily_ohlc_and_minute_cumulative_volume() -> None:
    start = datetime(2026, 6, 2, 9, 30)
    builder = DailyContextBuilder()
    bars = {
        "000938.SZ": [
            _bar("000938.SZ", Exchange.SZSE, start, 28.29),
            _bar("000938.SZ", Exchange.SZSE, start + timedelta(minutes=1), 28.64),
        ],
    }
    bars["000938.SZ"][0].volume = 2_000
    bars["000938.SZ"][1].volume = 3_000

    builder.add_day(
        bars,
        daily_prices={
            "000938.SZ": {
                "open": 28.29,
                "high": 28.69,
                "low": 27.68,
                "close": 28.21,
            },
        },
    )
    reference = builder.references(date(2026, 6, 3))["000938.SZ"]

    assert reference.previous_open == 28.29
    assert reference.previous_high == 28.69
    assert reference.previous_low == 27.68
    assert reference.previous_close == 28.21
    assert reference.previous_cumulative_volumes == {
        time(9, 30): 2_000,
        time(9, 31): 5_000,
    }


def test_daily_context_keeps_raw_execution_pre_close_separate_from_signal_prices() -> None:
    start = datetime(2026, 1, 5, 9, 30)
    builder = DailyContextBuilder()
    builder.add_day({
        "600000.SH": [
            _bar("600000.SH", Exchange.SSE, start, 10),
            _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=1), 11),
        ],
    })
    reference = builder.references(
        date(2026, 1, 6),
        execution_metadata={
            "600000.SH": {"pre_close": 10.25, "price_limit_pct": 0.10},
        },
    )["600000.SH"]
    assert reference.previous_close == 11
    assert reference.limit_reference_price == 10.25
    assert reference.price_limit_pct == 0.10


def test_opening_breakout_uses_close_breakout_and_dynamic_ma5() -> None:
    moment = datetime(2026, 1, 6, 9, 30)
    reference = DailyReference(
        previous_open=11.0,
        previous_close=10.0,
        previous_high=11.0,
        closes=(9.8, 10.0),
        previous_cumulative_volumes={time(9, 30): 100, time(9, 31): 160},
    )
    strategy = OpeningBreakoutPoolStrategy({})

    below = _bar("600000.SH", Exchange.SSE, moment, 10.9)
    below.volume = 100
    context = PortfolioContext(moment, 100_000, 0, {}, {"600000.SH": reference})
    assert strategy.on_minute({"600000.SH": below}, context) == []

    crossed = _bar("600000.SH", Exchange.SSE, moment + timedelta(minutes=1), 11.1)
    crossed.open_price = 10.9
    crossed.volume = 150
    crossed_context = PortfolioContext(crossed.datetime, 100_000, 0, {}, {"600000.SH": reference})
    intents = strategy.on_minute({"600000.SH": crossed}, crossed_context)
    assert [(item.symbol, item.direction) for item in intents] == [("600000.SH", Direction.LONG)]

    sell_reference = DailyReference(closes=(9.0, 10.0, 11.0, 12.0, 13.0))
    position = PortfolioPositionView("600000.SH", 100, 10.0, date(2026, 1, 5))
    above_ma = _bar("600000.SH", Exchange.SSE, datetime(2026, 1, 6, 10, 29), 13.0)
    above_context = PortfolioContext(above_ma.datetime, 90_000, 0, {"600000.SH": position}, {"600000.SH": sell_reference})
    assert strategy.on_minute({"600000.SH": above_ma}, above_context) == []

    sell_bar = _bar("600000.SH", Exchange.SSE, datetime(2026, 1, 6, 10, 30), 10.5)
    sell_context = PortfolioContext(sell_bar.datetime, 90_000, 0, {"600000.SH": position}, {"600000.SH": sell_reference})
    sell_intents = strategy.on_minute({"600000.SH": sell_bar}, sell_context)
    assert [(item.symbol, item.direction) for item in sell_intents] == [("600000.SH", Direction.SHORT)]

    still_below = _bar("600000.SH", Exchange.SSE, datetime(2026, 1, 6, 10, 31), 10.4)
    still_below_context = PortfolioContext(still_below.datetime, 90_000, 0, {"600000.SH": position}, {"600000.SH": sell_reference})
    still_below_intents = strategy.on_minute({"600000.SH": still_below}, still_below_context)
    assert [(item.symbol, item.direction) for item in still_below_intents] == [("600000.SH", Direction.SHORT)]


def test_both_opening_breakout_strategies_sell_without_prior_ma_cross() -> None:
    moment = datetime(2026, 1, 6, 9, 30)
    reference = DailyReference(closes=(9.0, 10.0, 11.0, 12.0, 13.0))
    position = PortfolioPositionView("600000.SH", 100, 10.0, date(2026, 1, 5))
    below_ma = _bar("600000.SH", Exchange.SSE, moment, 10.5)
    context = PortfolioContext(
        moment,
        90_000,
        0,
        {"600000.SH": position},
        {"600000.SH": reference},
    )

    for strategy_class in (OpeningBreakoutPoolStrategy, OpeningBreakoutCondition1Strategy):
        intents = strategy_class({}).on_minute({"600000.SH": below_ma}, context)
        assert [(item.symbol, item.direction) for item in intents] == [
            ("600000.SH", Direction.SHORT),
        ]


def test_opening_breakout_condition_2_requires_same_time_volume_surge() -> None:
    moment = datetime(2026, 1, 6, 9, 30)
    reference = DailyReference(
        previous_open=10.1,
        previous_close=10.0,
        previous_high=10.2,
        closes=(9.9, 10.2, 10.0),
        previous_cumulative_volumes={time(9, 30): 100},
    )
    context = PortfolioContext(moment, 100_000, 0, {}, {"600000.SH": reference})

    low_volume = _bar("600000.SH", Exchange.SSE, moment, 10.4)
    low_volume.volume = 149
    assert OpeningBreakoutPoolStrategy({"volume_multiple": 3.0}).on_minute(
        {"600000.SH": low_volume}, context
    ) == []

    enough_volume = _bar("600000.SH", Exchange.SSE, moment, 10.4)
    enough_volume.volume = 150
    intents = OpeningBreakoutPoolStrategy({"volume_multiple": 3.0}).on_minute(
        {"600000.SH": enough_volume}, context
    )
    assert len(intents) == 1
    assert any("2" in label for label in intents[0].diagnostic["matched_conditions"])


def test_opening_breakout_condition_2_accepts_today_above_five_percent_but_requires_two_prior_gains_below_five() -> None:
    moment = datetime(2026, 1, 6, 9, 30)

    def _context(closes: tuple[float, float, float]) -> PortfolioContext:
        reference = DailyReference(
            previous_open=9.9,
            previous_close=10.0,
            previous_high=11.0,
            closes=closes,
            previous_cumulative_volumes={time(9, 30): 100},
        )
        return PortfolioContext(moment, 100_000, 0, {}, {"600000.SH": reference})

    bar = _bar("600000.SH", Exchange.SSE, moment, 10.8)  # Today is up 8%.
    bar.volume = 150
    intents = OpeningBreakoutPoolStrategy({}).on_minute(
        {"600000.SH": bar}, _context((9.5, 9.8, 10.0))
    )
    assert any("条件2" in label for label in intents[0].diagnostic["matched_conditions"])

    # The day before yesterday was exactly up 5%, which must fail the strict <5% rule.
    failed_intents = OpeningBreakoutPoolStrategy({}).on_minute(
        {"600000.SH": bar}, _context((9.5, 9.975, 10.0))
    )
    assert not any("条件2" in label for item in failed_intents for label in item.diagnostic["matched_conditions"])


def test_opening_breakout_condition_3_keeps_three_percent_ceiling() -> None:
    moment = datetime(2026, 1, 6, 9, 30)

    def _reference(previous_previous_close: float) -> DailyReference:
        return DailyReference(
            previous_open=10.1,
            previous_close=10.0,
            previous_high=10.5,
            closes=(previous_previous_close, 10.0),
            previous_cumulative_volumes={time(9, 30): 100},
        )

    bar = _bar("600000.SH", Exchange.SSE, moment, 10.1)
    bar.volume = 150
    under_three = PortfolioContext(moment, 100_000, 0, {}, {"600000.SH": _reference(10 / 1.029)})
    assert OpeningBreakoutPoolStrategy({"volume_multiple": 1.5}).on_minute(
        {"600000.SH": bar}, under_three
    )

    at_three = PortfolioContext(moment, 100_000, 0, {}, {"600000.SH": _reference(10 / 1.03)})
    assert OpeningBreakoutPoolStrategy({"volume_multiple": 1.5}).on_minute(
        {"600000.SH": bar}, at_three
    ) == []


def test_opening_breakout_condition_1_ignores_conditions_2_and_3() -> None:
    moment = datetime(2026, 1, 6, 9, 30)
    reference = DailyReference(
        previous_open=11.0,
        previous_close=10.0,
        previous_high=11.0,
        closes=(9.8, 10.0),
        previous_cumulative_volumes={time(9, 30): 100},
    )
    context = PortfolioContext(moment, 100_000, 0, {}, {"600000.SH": reference})

    # This meets original condition 2 only: current gain is 4%, but it neither
    # breaks yesterday's high nor reaches the required volume multiple.
    condition_2_bar = _bar("600000.SH", Exchange.SSE, moment, 10.4)
    condition_2_bar.volume = 100
    assert OpeningBreakoutCondition1Strategy({"volume_multiple": 1.5}).on_minute(
        {"600000.SH": condition_2_bar}, context
    ) == []

    condition_1_strategy = OpeningBreakoutCondition1Strategy({"volume_multiple": 1.5})
    below = _bar("600000.SH", Exchange.SSE, moment, 10.9)
    below.volume = 100
    assert condition_1_strategy.on_minute({"600000.SH": below}, context) == []

    condition_1_bar = _bar("600000.SH", Exchange.SSE, moment + timedelta(minutes=1), 11.1)
    condition_1_bar.open_price = 10.9
    condition_1_bar.volume = 150
    second_reference = DailyReference(
        previous_open=11.0,
        previous_close=10.0,
        previous_high=11.0,
        closes=(9.8, 10.0),
        previous_cumulative_volumes={time(9, 30): 100, time(9, 31): 160},
    )
    second_context = PortfolioContext(condition_1_bar.datetime, 100_000, 0, {}, {"600000.SH": second_reference})
    intents = condition_1_strategy.on_minute({"600000.SH": condition_1_bar}, second_context)
    assert [(item.symbol, item.direction) for item in intents] == [("600000.SH", Direction.LONG)]
    assert intents[0].diagnostic["matched_conditions"] == ["条件1：阴线后同期累计量达1.5倍并突破昨日高点"]


def test_repository_streams_only_non_empty_minute_days() -> None:
    class _Repo:
        def get_minute_range(self, symbols, start, end):
            return __import__("polars").DataFrame({"symbol": [symbols[0]]}) if start.day == 5 else __import__("polars").DataFrame()

    dates = list(KlineRepository.iter_minute_days(_Repo(), ["600000.SH"], datetime(2026, 1, 5).date(), datetime(2026, 1, 7).date()))
    assert [item[0].isoformat() for item in dates] == ["2026-01-05"]


def test_repository_reads_standard_minute_partition_directly(tmp_path) -> None:
    part = tmp_path / "kline_minute" / "date=2026-01-05"
    part.mkdir(parents=True)
    __import__("polars").DataFrame({
        "symbol": ["600000.SH", "300001.SZ"],
        "datetime": [datetime(2026, 1, 5, 9, 30), datetime(2026, 1, 5, 9, 30)],
        "open": [10.0, 20.0], "high": [10.0, 20.0], "low": [10.0, 20.0], "close": [10.0, 20.0],
        "volume": [100.0, 100.0], "amount": [1000.0, 2000.0],
    }).write_parquet(part / "part.parquet")
    repo = object.__new__(KlineRepository)
    repo.store = SimpleNamespace(data_dir=tmp_path)
    result = repo.get_minute_range(["600000.SH"], date(2026, 1, 5), date(2026, 1, 5))
    assert result["symbol"].to_list() == ["600000.SH"]
    assert repo.minute_trading_days(date(2026, 1, 1), date(2026, 1, 7)) == [date(2026, 1, 5)]


def test_portfolio_metrics_include_return_drawdown_costs_and_trade_quality() -> None:
    fills = [
        PortfolioFill("600000.SH", Direction.LONG, datetime(2026, 1, 5, 9, 31), 10, 100, "entry", 5, 0, 10),
        PortfolioFill("600000.SH", Direction.SHORT, datetime(2026, 1, 6, 9, 31), 11, 100, "exit", 5, 11, 11),
    ]
    metrics, drawdowns = VnpyMinuteBacktestService._portfolio_metrics(
        initial_capital=100_000,
        equity_curve=[
            {"date": "2026-01-05 15:00:00", "value": 100_000},
            {"date": "2026-01-06 15:00:00", "value": 110_000},
            {"date": "2026-01-07 15:00:00", "value": 99_000},
        ],
        completed_trades=[{"pnl_amount": 1000, "pnl_pct": 0.1}],
        fills=fills,
        trading_days=3,
    )
    assert metrics["annual_return"] is not None
    assert metrics["max_drawdown"] == -0.1
    assert metrics["win_rate"] == 1
    assert metrics["commission"] == 10
    assert metrics["stamp_tax"] == 11
    assert metrics["slippage_cost"] == 21
    assert len(drawdowns) == 3


def test_portfolio_result_helpers_keep_names_positions_and_trading_durations() -> None:
    entry_at = datetime(2026, 1, 5, 9, 31)
    exit_at = datetime(2026, 1, 6, 9, 31)
    fills = [
        PortfolioFill("600000.SH", Direction.LONG, entry_at, 10, 100, "entry", 5, 0, 0,
                      portfolio_equity_before=100_000, entry_position_pct=0.01005, signal_id=1),
        PortfolioFill("600000.SH", Direction.SHORT, exit_at, 11, 100, "exit", 5, 11, 0, signal_id=2),
    ]
    curve = [
        {"date": "2026-01-05 09:31:00", "value": 99_995},
        {"date": "2026-01-05 09:32:00", "value": 100_000},
        {"date": "2026-01-06 09:31:00", "value": 100_079},
    ]
    timeline = VnpyMinuteBacktestService._timeline_indexes(curve)
    trades = VnpyMinuteBacktestService._portfolio_completed_trades(fills, {"600000.SH": "浦发银行"}, timeline)
    assert trades[0]["name"] == "浦发银行"
    assert trades[0]["position_pct"] == 0.01005
    assert trades[0]["duration"] == 1
    assert trades[0]["duration_minutes"] == 2
    ledger = VnpyMinuteBacktestService._daily_ledger(fills, trades, curve, {"600000.SH": "浦发银行"}, 100_000)
    assert ledger[0]["buy_count"] == 1
    assert ledger[1]["sell_count"] == 1
    assert ledger[1]["realized_pnl"] == trades[0]["pnl_amount"]


def test_vnpy_result_serializes_directions_as_stable_api_codes() -> None:
    fill = PortfolioFill("600000.SH", Direction.LONG, datetime(2026, 1, 5, 9, 31), 10, 100, "entry", 0, 0, 0)
    payload = VnpyMinuteBacktestService._portfolio_fill_to_trade(fill)
    assert payload["direction"] == "LONG"


def test_daily_sell_fill_displays_net_cash_after_commission_and_stamp_tax() -> None:
    fill = PortfolioFill("600000.SH", Direction.SHORT, datetime(2026, 1, 6, 9, 31), 11, 100, "exit", 5, 11, 0)
    payload = VnpyMinuteBacktestService._portfolio_fill_to_trade(fill)
    assert payload["direction"] == "SHORT"
    assert payload["entry_value"] == 1_084


def test_portfolio_engine_records_signal_lifecycle() -> None:
    start = datetime(2026, 1, 5, 9, 30)
    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=10_000, commission_rate=0, stamp_tax_rate=0,
        slippage_rate=0, min_commission=0, max_volume_ratio=None, reserve_ratio=0,
    )

    class _SignalStrategy:
        def on_minute(self, bars, context):
            if context.timestamp == start:
                return [OrderIntent("600000.SH", Direction.LONG, "entry", diagnostic={"matched_conditions": ["测试条件"]})]
            return []

    engine.run_day({"600000.SH": [_bar("600000.SH", Exchange.SSE, start), _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=1))]}, _SignalStrategy(), {})
    result = engine.result()
    assert result.signals[0].status == "filled"
    assert result.signals[0].diagnostic["matched_conditions"] == ["测试条件"]
    assert result.fills[0].entry_position_pct is not None


def test_portfolio_engine_limits_positions_and_prioritizes_multi_condition_signal() -> None:
    start = datetime(2026, 1, 5, 9, 30)
    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=100_000, commission_rate=0, stamp_tax_rate=0,
        slippage_rate=0, min_commission=0, max_volume_ratio=None, reserve_ratio=0,
        max_positions=1,
    )

    class _PriorityStrategy:
        def on_minute(self, bars, context):
            if context.timestamp != start:
                return []
            return [
                OrderIntent("600000.SH", Direction.LONG, "entry", diagnostic={"matched_conditions": ["条件1"]}),
                OrderIntent("300001.SZ", Direction.LONG, "entry", diagnostic={"matched_conditions": ["条件1", "条件2"]}),
            ]

    bars = {
        "600000.SH": [_bar("600000.SH", Exchange.SSE, start), _bar("600000.SH", Exchange.SSE, start + timedelta(minutes=1))],
        "300001.SZ": [_bar("300001.SZ", Exchange.SZSE, start), _bar("300001.SZ", Exchange.SZSE, start + timedelta(minutes=1))],
    }
    engine.run_day(bars, _PriorityStrategy(), {})
    result = engine.result()
    assert [fill.symbol for fill in result.fills] == ["300001.SZ"]
    assert result.signals[0].status == "rejected"
    assert result.signals[0].rejection_reason == "max_positions"
    assert result.signals[1].status == "filled"


def test_portfolio_tries_same_minute_fallback_when_primary_cannot_fill() -> None:
    start = datetime(2026, 1, 5, 9, 30)
    fill_time = start + timedelta(minutes=1)
    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=100_000, commission_rate=0, stamp_tax_rate=0,
        slippage_rate=0, min_commission=0, max_volume_ratio=None, reserve_ratio=0,
        max_positions=1,
    )

    class _FallbackStrategy:
        def on_minute(self, bars, context):
            if context.timestamp != start:
                return []
            return [
                OrderIntent("600000.SH", Direction.LONG, "entry", diagnostic={"matched_conditions": ["条件1", "条件2"]}),
                OrderIntent("000003.SZ", Direction.LONG, "entry", diagnostic={"matched_conditions": ["条件1"]}),
                OrderIntent("000002.SZ", Direction.LONG, "entry", diagnostic={"matched_conditions": ["条件1"]}),
            ]

    bars = {
        "600000.SH": [_bar("600000.SH", Exchange.SSE, start, 10), _bar("600000.SH", Exchange.SSE, fill_time, 11)],
        "000003.SZ": [_bar("000003.SZ", Exchange.SZSE, start, 10), _bar("000003.SZ", Exchange.SZSE, fill_time, 10)],
        "000002.SZ": [_bar("000002.SZ", Exchange.SZSE, start, 10), _bar("000002.SZ", Exchange.SZSE, fill_time, 10)],
    }
    references = {symbol: DailyReference(previous_close=10) for symbol in bars}
    engine.run_day(bars, _FallbackStrategy(), references)

    result = engine.result()
    assert [(fill.symbol, fill.datetime) for fill in result.fills] == [("000002.SZ", fill_time)]
    statuses = {signal.symbol: (signal.status, signal.rejection_reason) for signal in result.signals}
    assert statuses["600000.SH"] == ("rejected", "price_limit")
    assert statuses["000002.SZ"] == ("filled", None)
    assert statuses["000003.SZ"] == ("rejected", "max_positions")


def test_limit_up_primary_uses_raw_pre_close_and_falls_back_like_002938_case() -> None:
    start = datetime(2026, 5, 22, 9, 30)
    fill_time = start + timedelta(minutes=1)
    engine = MultiSymbolNextBarOpenEngine(
        initial_cash=1_100_000, commission_rate=0, stamp_tax_rate=0,
        slippage_rate=0, min_commission=0, max_volume_ratio=None, reserve_ratio=0,
        max_positions=1,
    )

    class _ActualCaseStrategy:
        def on_minute(self, bars, context):
            if context.timestamp != start:
                return []
            return [
                OrderIntent("002938.SZ", Direction.LONG, "entry", diagnostic={"matched_conditions": ["条件1", "条件2"]}),
                OrderIntent("605376.SH", Direction.LONG, "entry", diagnostic={"matched_conditions": ["条件1", "条件3"]}),
            ]

    bars = {
        "002938.SZ": [
            _bar("002938.SZ", Exchange.SZSE, start, 103.92),
            _bar("002938.SZ", Exchange.SZSE, fill_time, 103.93),
        ],
        "605376.SH": [
            _bar("605376.SH", Exchange.SSE, start, 150.96),
            _bar("605376.SH", Exchange.SSE, fill_time, 150.51),
        ],
    }
    references = {
        "002938.SZ": DailyReference(
            previous_close=94.48,
            limit_reference_price=94.48,
            price_limit_pct=0.10,
        ),
        "605376.SH": DailyReference(
            previous_close=139.36,
            limit_reference_price=139.36,
            price_limit_pct=0.10,
        ),
    }
    engine.run_day(bars, _ActualCaseStrategy(), references)

    result = engine.result()
    assert [(fill.symbol, fill.datetime) for fill in result.fills] == [
        ("605376.SH", fill_time),
    ]
    statuses = {signal.symbol: (signal.status, signal.rejection_reason) for signal in result.signals}
    assert statuses["002938.SZ"] == ("rejected", "price_limit")
    assert statuses["605376.SH"] == ("filled", None)
