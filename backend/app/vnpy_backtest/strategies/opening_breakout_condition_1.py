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
        reference = context.daily_references.get(symbol)
        price = float(bar.close_price)
        if reference is None or price <= 0 or reference.previous_close is None:
            return None

        previous_same_time_volume = reference.previous_cumulative_volumes.get(bar.datetime.time())
        current_volume = self._today_cumulative_volume[symbol]
        has_volume_surge = (
            previous_same_time_volume is not None
            and previous_same_time_volume > 0
            and current_volume >= previous_same_time_volume * self.volume_multiple
        )
        condition_1 = bool(
            reference.previous_high is not None
            and reference.previous_open is not None
            and reference.previous_close < reference.previous_open
            and has_volume_surge
            and self._crossed_above(symbol, bar, reference.previous_high)
        )
        if not condition_1:
            return None
        return {
            "matched_conditions": ["条件1：阴线后同期累计量达1.5倍并突破昨日高点"],
            "signal_price": round(price, 6),
        }
