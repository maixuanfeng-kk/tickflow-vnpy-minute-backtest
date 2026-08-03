"""The single source of truth for selectable vn.py backtest strategies."""
from __future__ import annotations

from app.vnpy_backtest.strategies.base import StrategyKind, StrategyParameter, StrategySpec
from app.vnpy_backtest.strategies.opening_breakout_condition_1 import OpeningBreakoutCondition1Strategy
from app.vnpy_backtest.strategies.opening_breakout_condition_2 import OpeningBreakoutCondition2Strategy
from app.vnpy_backtest.strategies.opening_breakout_condition_3 import OpeningBreakoutCondition3Strategy
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy


_COMMON_PARAMETERS = (
    StrategyParameter("scan_start_time", "买入开始时间", "09:30", kind="time"),
    StrategyParameter("scan_end_time", "买入结束时间", "10:00", kind="time"),
    StrategyParameter("stop_loss_pct", "止损比例", 0.02, minimum=0),
    StrategyParameter("ma_exit_period", "动态均线窗口", 5, kind="int", minimum=2),
)


_STRATEGIES: dict[str, StrategySpec] = {
    "opening_volume_portfolio": StrategySpec(
        id="opening_volume_portfolio",
        name="开盘突破股票池（vn.py）",
        kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutPoolStrategy,
        min_symbols=1,
        max_symbols=1000,
        description="09:30–10:00 的 A 股早盘放量策略，命中本地定义的条件 1、2 或 3 即入场。",
        parameters=_COMMON_PARAMETERS,
    ),
    "opening_breakout_condition_1": StrategySpec(
        id="opening_breakout_condition_1", name="开盘突破条件 1（vn.py）", kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutCondition1Strategy, min_symbols=1, max_symbols=1000,
        description="仅保留前日阴线、1.5 倍同期累计量与盘中突破昨日高点的入场条件。",
        parameters=_COMMON_PARAMETERS,
    ),
    "opening_breakout_condition_2": StrategySpec(
        id="opening_breakout_condition_2", name="开盘突破条件 2（vn.py）", kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutCondition2Strategy, min_symbols=1, max_symbols=1000,
        description="仅保留当日涨幅大于 3%、前两日涨幅小于 5%且 1.5 倍同期累计量的入场条件。",
        parameters=_COMMON_PARAMETERS,
    ),
    "opening_breakout_condition_3": StrategySpec(
        id="opening_breakout_condition_3", name="开盘突破条件 3（vn.py）", kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutCondition3Strategy, min_symbols=1, max_symbols=1000,
        description="仅保留前日上涨小于 3%且 1.5 倍同期累计量的入场条件。",
        parameters=_COMMON_PARAMETERS,
    ),
}


def get_strategy(strategy_id: str) -> StrategySpec | None:
    return _STRATEGIES.get(strategy_id)


def list_strategies() -> list[StrategySpec]:
    return list(_STRATEGIES.values())
