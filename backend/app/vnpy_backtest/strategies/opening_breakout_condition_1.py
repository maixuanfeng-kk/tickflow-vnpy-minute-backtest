"""Condition-1-only variant of the opening-breakout stock-pool strategy."""
from __future__ import annotations

from typing import Mapping

from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import PortfolioContext
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy


class OpeningBreakoutCondition1Strategy(OpeningBreakoutPoolStrategy):
    """Keep only condition 1 for entries; execution and exits stay unchanged."""

    def _buy_diagnostic(
        self,
        symbol: str,
        bar: BarData,
        context: PortfolioContext,
    ) -> dict[str, object] | None:
        diagnostic = super()._buy_diagnostic(symbol, bar, context)
        if not diagnostic or "condition_1" not in diagnostic.get("matched_condition_ids", []):
            return None
        return {
            **diagnostic,
            "matched_conditions": [self._condition_label("condition_1")],
            "matched_condition_ids": ["condition_1"],
            "primary_reason": "condition_1",
        }
