"""Registry for selectable, code-backed stock-pool strategies."""
from __future__ import annotations

from app.stock_pools.base import StockPoolStrategySpec
from app.stock_pools.strategies import MonthlyGrowthTrendStrategy

_STRATEGIES = {
    "monthly_growth_trend": StockPoolStrategySpec(
        id="monthly_growth_trend",
        name="成长趋势月度股票池",
        description="条件一：总市值大于300亿元、营收同比大于15%、最近5日每天开盘或收盘价在MA60上方。条件二：总市值大于300亿元、最近一期净利润大于5000万元，并在最近20个交易日内创200日新高。",
        strategy_class=MonthlyGrowthTrendStrategy,
        parameters=(
            {"id": "market_cap_min", "key": "market_cap_min", "label": "最低总市值", "type": "number", "default": 30_000_000_000, "min": 0, "step": 1_000_000_000, "unit": "元"},
            {"id": "revenue_yoy_min", "key": "revenue_yoy_min", "label": "最低营收同比", "type": "number", "default": 0.15, "min": 0, "max": 10, "step": 0.01, "unit": "比例"},
            {"id": "net_profit_min", "key": "net_profit_min", "label": "最低净利润", "type": "number", "default": 50_000_000, "min": 0, "step": 1_000_000, "unit": "元"},
        ),
        version="4",
    ),
}


def get_strategy(strategy_id: str) -> StockPoolStrategySpec | None:
    return _STRATEGIES.get(strategy_id)


def list_strategies() -> list[StockPoolStrategySpec]:
    return list(_STRATEGIES.values())
