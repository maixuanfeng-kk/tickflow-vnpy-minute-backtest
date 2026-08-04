"""Condition-3-only variant of the opening-breakout stock-pool strategy."""
from __future__ import annotations

from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import PortfolioContext
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy


class OpeningBreakoutCondition3Strategy(OpeningBreakoutPoolStrategy):
    """Keep only condition 3 for entries; execution and exits stay unchanged."""

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
        previous_gain = self._previous_day_gain(reference)
        condition_3 = bool(
            previous_gain is not None
            and 0 < previous_gain < 0.03
            and has_volume_surge
        )
        if not condition_3:
            return None
        return {
            "matched_conditions": ["条件3：前日上涨<3%且同期累计量达1.5倍"],
            "signal_price": round(price, 6),
        }
