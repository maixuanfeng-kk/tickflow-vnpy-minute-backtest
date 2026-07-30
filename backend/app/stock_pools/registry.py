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
        parameters=(
            {"key": "revenue_yoy_min", "label": "营收同比下限", "type": "number", "default": 0.15, "min": 0, "step": 0.01},
            {"key": "ma_window", "label": "均线周期", "type": "number", "default": 60, "min": 1, "step": 1},
            {"key": "ma_days", "label": "连续站上均线天数", "type": "number", "default": 5, "min": 1, "step": 1},
            {"key": "high_window", "label": "新高回看交易日", "type": "number", "default": 200, "min": 1, "step": 1},
            {"key": "high_days", "label": "新高有效交易日", "type": "number", "default": 20, "min": 1, "step": 1},
            {"key": "listing_days_min", "label": "最少上市交易日", "type": "number", "default": 250, "min": 1, "step": 1},
            {"key": "market_cap_min", "label": "最小总市值", "type": "number", "default": 10_000_000_000, "min": 0, "step": 100_000_000},
        ),
    ),
}


def get_strategy(strategy_id: str) -> StockPoolStrategySpec | None:
    return _STRATEGIES.get(strategy_id)


def list_strategies() -> list[StockPoolStrategySpec]:
    return list(_STRATEGIES.values())
