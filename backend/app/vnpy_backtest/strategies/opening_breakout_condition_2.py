"""Condition-2-only variant of the opening-breakout stock-pool strategy."""
from __future__ import annotations

from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import PortfolioContext
from app.vnpy_backtest.strategies.opening_breakout_pool import OpeningBreakoutPoolStrategy


class OpeningBreakoutCondition2Strategy(OpeningBreakoutPoolStrategy):
    """Keep only condition 2 for entries; execution and exits stay unchanged."""

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
        pre_previous_gain = self._pre_previous_day_gain(reference)
        today_gain = price / reference.previous_close - 1
        condition_2 = bool(
            previous_gain is not None
            and pre_previous_gain is not None
            and today_gain > 0.03
            and previous_gain < 0.05
            and pre_previous_gain < 0.05
            and has_volume_surge
        )
        if not condition_2:
            return None
        return {
            "matched_conditions": ["条件2：当日涨幅>3%、昨日及前日涨幅<5%且同期累计量达1.5倍"],
            "signal_price": round(price, 6),
        }
