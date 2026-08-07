"""The single source of truth for selectable vn.py backtest strategies."""
from __future__ import annotations

from app.vnpy_backtest.strategies.base import StrategyKind, StrategyParameter, StrategySpec
from app.vnpy_backtest.strategies.etf_159915_minute import Etf159915MinuteStrategy
from app.vnpy_backtest.strategies.opening_breakout_condition_1 import OpeningBreakoutCondition1Strategy
from app.vnpy_backtest.strategies.opening_breakout_condition_2 import OpeningBreakoutCondition2Strategy
from app.vnpy_backtest.strategies.opening_breakout_condition_3 import OpeningBreakoutCondition3Strategy
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy


_STRATEGIES: dict[str, StrategySpec] = {
    "etf_159915_minute": StrategySpec(
        id="etf_159915_minute",
        name="159915 ETF 分钟交易策略",
        kind=StrategyKind.PORTFOLIO,
        strategy_class=Etf159915MinuteStrategy,
        min_symbols=1,
        max_symbols=1,
        description="159915 raw 1m ETF strategy with documented dynamic entry and exit protection.",
    ),
    "opening_breakout_pool": StrategySpec(
        id="opening_breakout_pool",
        name="开盘突破股票池（vn.py）",
        kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutPoolStrategy,
        min_symbols=1,
        max_symbols=1000,
        description="09:30–10:00 的 A 股开盘突破股票池策略，使用本地分钟数据。",
        parameters=(
            StrategyParameter("buy_start", "买入开始时间", "09:30", kind="time"),
            StrategyParameter("buy_end", "买入结束时间", "10:00", kind="time"),
            StrategyParameter("stop_loss", "止损比例", 0.02, minimum=0),
            StrategyParameter("ma_window", "动态均线窗口", 5, kind="int", minimum=2),
        ),
    ),
    "opening_breakout_condition_1": StrategySpec(
        id="opening_breakout_condition_1",
        name="开盘突破条件1（vn.py）",
        kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutCondition1Strategy,
        min_symbols=1,
        max_symbols=1000,
        description="仅保留开盘突破股票池的条件1：阴线后放量突破昨日高点；其余回测规则保持一致。",
        parameters=(
            StrategyParameter("buy_start", "买入开始时间", "09:30", kind="time"),
            StrategyParameter("buy_end", "买入结束时间", "10:00", kind="time"),
            StrategyParameter("stop_loss", "止损比例", 0.02, minimum=0),
            StrategyParameter("ma_window", "动态均线窗口", 5, kind="int", minimum=2),
        ),
    ),
    "opening_breakout_condition_2": StrategySpec(
        id="opening_breakout_condition_2",
        name="开盘突破条件2（vn.py）",
        kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutCondition2Strategy,
        min_symbols=1,
        max_symbols=1000,
        description="仅保留开盘突破股票池的条件2：当日涨幅大于3%、昨日及前日涨幅小于5%，且同期累计量达1.5倍；其余回测规则保持一致。",
        parameters=(
            StrategyParameter("buy_start", "买入开始时间", "09:30", kind="time"),
            StrategyParameter("buy_end", "买入结束时间", "10:00", kind="time"),
            StrategyParameter("stop_loss", "止损比例", 0.02, minimum=0),
            StrategyParameter("ma_window", "动态均线窗口", 5, kind="int", minimum=2),
        ),
    ),
    "opening_breakout_condition_3": StrategySpec(
        id="opening_breakout_condition_3",
        name="开盘突破条件3（vn.py）",
        kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutCondition3Strategy,
        min_symbols=1,
        max_symbols=1000,
        description="仅保留开盘突破股票池的条件3：前日上涨小于3%且同期累计量达1.5倍；其余回测规则保持一致。",
        parameters=(
            StrategyParameter("buy_start", "买入开始时间", "09:30", kind="time"),
            StrategyParameter("buy_end", "买入结束时间", "10:00", kind="time"),
            StrategyParameter("stop_loss", "止损比例", 0.02, minimum=0),
            StrategyParameter("ma_window", "动态均线窗口", 5, kind="int", minimum=2),
        ),
    ),
}


def get_strategy(strategy_id: str) -> StrategySpec | None:
    return _STRATEGIES.get(strategy_id)


def list_strategies() -> list[StrategySpec]:
    return list(_STRATEGIES.values())
