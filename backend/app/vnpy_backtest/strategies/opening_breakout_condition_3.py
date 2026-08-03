"""Condition-3-only variant of the opening-breakout strategy."""
from __future__ import annotations

from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import PortfolioContext
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy


class OpeningBreakoutCondition3Strategy(OpeningBreakoutPoolStrategy):
    """Keep only the local condition 3 for entries; exits stay unchanged."""

    def _buy_diagnostic(self, symbol: str, bar: BarData, context: PortfolioContext) -> dict[str, object] | None:
        diagnostic = super()._buy_diagnostic(symbol, bar, context)
        if not diagnostic or "condition_3" not in diagnostic.get("matched_condition_ids", []):
            return None
        return {
            **diagnostic,
            "matched_conditions": [self._condition_label("condition_3")],
            "matched_condition_ids": ["condition_3"],
            "primary_reason": "condition_3",
        }
