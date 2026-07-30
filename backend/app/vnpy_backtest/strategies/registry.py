"""The single source of truth for selectable vn.py backtest strategies."""
from __future__ import annotations

from app.vnpy_backtest.strategies.base import StrategyKind, StrategyParameter, StrategySpec
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy


_STRATEGIES: dict[str, StrategySpec] = {
    "opening_volume_portfolio": StrategySpec(
        id="opening_volume_portfolio",
        name="开盘突破股票池（vn.py）",
        kind=StrategyKind.PORTFOLIO,
        strategy_class=OpeningBreakoutPoolStrategy,
        min_symbols=1,
        max_symbols=1000,
        description="09:30–09:59 的 A 股早盘放量股票池策略，使用本地分钟数据。",
        parameters=(
            StrategyParameter("buy_start", "买入开始时间", "09:30", kind="time"),
            StrategyParameter("buy_end", "买入结束时间", "09:59", kind="time"),
            StrategyParameter("stop_loss", "止损比例", 0.02, minimum=0),
            StrategyParameter("ma_window", "动态均线窗口", 5, kind="int", minimum=2),
        ),
    ),
}


def get_strategy(strategy_id: str) -> StrategySpec | None:
    return _STRATEGIES.get(strategy_id)


def list_strategies() -> list[StrategySpec]:
    return list(_STRATEGIES.values())
