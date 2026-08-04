"""Registry for selectable, code-backed stock-pool strategies."""
from __future__ import annotations

from app.stock_pools.base import StockPoolStrategySpec
from app.stock_pools.strategies import MonthlyGrowthTrendStrategy

_STRATEGIES = {
    "monthly_growth_trend": StockPoolStrategySpec(
        id="monthly_growth_trend",
        name="成长趋势月度股票池",
        description="固定使用选股日静态MA60和静态200日新高触及，排除当日ST/*ST、上市未满250个交易日和市值不超过100亿元的股票。",
        strategy_class=MonthlyGrowthTrendStrategy,
        parameters=(),
        version="3",
    ),
}


def get_strategy(strategy_id: str) -> StockPoolStrategySpec | None:
    return _STRATEGIES.get(strategy_id)


def list_strategies() -> list[StockPoolStrategySpec]:
    return list(_STRATEGIES.values())
