"""Registry for selectable, code-backed stock-pool strategies."""
from __future__ import annotations

from app.stock_pools.base import StockPoolStrategySpec
from app.stock_pools.strategies import MonthlyGrowthTrendStrategy

_STRATEGIES = {
    "monthly_growth_trend": StockPoolStrategySpec(
        id="monthly_growth_trend",
        name="成长趋势月度股票池",
        description="排除当日ST/*ST、上市满250个交易日、市值大于100亿元，且满足营收增长趋势或盈利新高条件的月度股票池。",
        strategy_class=MonthlyGrowthTrendStrategy,
        parameters=(),
    ),
}


def get_strategy(strategy_id: str) -> StockPoolStrategySpec | None:
    return _STRATEGIES.get(strategy_id)


def list_strategies() -> list[StockPoolStrategySpec]:
    return list(_STRATEGIES.values())
