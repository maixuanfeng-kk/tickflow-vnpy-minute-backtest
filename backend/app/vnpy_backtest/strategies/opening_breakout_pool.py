"""Opening-breakout A-share pool strategy for the local vn.py portfolio runner."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, time
from typing import Mapping, Sequence

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import OrderIntent, PortfolioContext


class OpeningBreakoutPoolStrategy:
    """Signal-only implementation of the user's opening-breakout rules."""

    VOLUME_MULTIPLE = 1.5

    def __init__(self, params: Mapping[str, object]) -> None:
        # All three entry conditions use the same fixed 1.5x
        # same-minute cumulative-volume threshold.
        self.volume_multiple = self.VOLUME_MULTIPLE
        self.buy_start = time.fromisoformat(str(params.get("buy_start", "09:30")))
        self.buy_end = time.fromisoformat(str(params.get("buy_end", "10:00")))
        self.stop_loss = abs(float(params.get("stop_loss", 0.02)))
        self.ma_window = int(params.get("ma_window", 5))
        self._session_date: date | None = None
        self._today_cumulative_volume: dict[str, float] = defaultdict(float)
        self._previous_minute_close: dict[str, float] = {}
        self._previous_ma_gap: dict[str, float] = {}

    def on_minute(
        self,
        bars: Mapping[str, BarData],
        context: PortfolioContext,
    ) -> Sequence[OrderIntent]:
        if self._session_date != context.timestamp.date():
            self._session_date = context.timestamp.date()
            self._today_cumulative_volume.clear()

        if context.timestamp.time() <= self.buy_end:
            for symbol, bar in bars.items():
                self._today_cumulative_volume[symbol] += max(float(bar.volume), 0.0)

        intents: list[OrderIntent] = []
        # Sell checks run through the whole session. The T+1 guard avoids a
        # rejected sell signal on every minute of the purchase day.
        held_symbols = set(context.positions)
        for symbol in tuple(self._previous_ma_gap):
            if symbol not in held_symbols:
                self._previous_ma_gap.pop(symbol, None)
        for symbol, position in context.positions.items():
            bar = bars.get(symbol)
            if bar is None:
                continue
            exit_diagnostic = self._sell_diagnostic(
                symbol,
                float(bar.close_price),
                position.average_cost,
                context.daily_references.get(symbol),
            )
            if exit_diagnostic and position.entry_date != context.timestamp.date():
                intents.append(OrderIntent(symbol, Direction.SHORT, "opening_breakout_exit", diagnostic=exit_diagnostic))

        if self.buy_start <= context.timestamp.time() <= self.buy_end:
            for symbol, bar in bars.items():
                if symbol not in context.positions:
                    diagnostic = self._buy_diagnostic(symbol, bar, context)
                    if diagnostic:
                        intents.append(OrderIntent(symbol, Direction.LONG, "opening_breakout_entry", diagnostic=diagnostic))

        # Crossing decisions above must see the previous completed minute.
        # Update the state only after all signals for the current minute have
        # been evaluated.
        for symbol, bar in bars.items():
            if float(bar.close_price) > 0:
                self._previous_minute_close[symbol] = float(bar.close_price)
        return intents

    def _buy_diagnostic(self, symbol: str, bar: BarData, context: PortfolioContext) -> dict[str, object] | None:
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
        today_gain = price / reference.previous_close - 1

        condition_1 = bool(
            reference.previous_high is not None
            and reference.previous_open is not None
            and reference.previous_close < reference.previous_open
            and has_volume_surge
            and self._crossed_above(symbol, bar, reference.previous_high)
        )
        condition_2 = bool(
            previous_gain is not None
            and 0.03 < today_gain < 0.05
            and previous_gain < 0.05
            and has_volume_surge
        )
        condition_3 = bool(
            previous_gain is not None
            and 0 < previous_gain < 0.03
            and has_volume_surge
        )
        matched = [
            label for label, passed in (
                ("条件1：阴线后同期累计量达1.5倍并突破昨日高点", condition_1),
                ("条件2：当日涨幅3%-5%、前日涨幅<5%且同期累计量达1.5倍", condition_2),
                ("条件3：前日上涨<3%且同期累计量达1.5倍", condition_3),
            ) if passed
        ]
        if not matched:
            return None
        return {"matched_conditions": matched, "signal_price": round(price, 6)}

    @staticmethod
    def _previous_day_gain(reference) -> float | None:
        if len(reference.closes) < 2 or reference.closes[-2] <= 0 or reference.previous_close is None:
            return None
        return reference.previous_close / reference.closes[-2] - 1

    def _crossed_above(self, symbol: str, bar: BarData, level: float) -> bool:
        previous_price = self._previous_minute_close.get(symbol, float(bar.open_price))
        return previous_price <= level < float(bar.close_price)

    def _sell_diagnostic(
        self,
        symbol: str,
        current_price: float,
        cost_price: float,
        reference,
    ) -> dict[str, object] | None:
        if current_price <= 0 or cost_price <= 0:
            return None
        crossed_below_ma = False
        dynamic_ma = None
        if reference is not None and self.ma_window > 1 and len(reference.closes) >= self.ma_window - 1:
            # Dynamic MA5: four completed daily closes plus this minute's close.
            dynamic_ma = (sum(reference.closes[-(self.ma_window - 1):]) + current_price) / self.ma_window
            current_gap = current_price - dynamic_ma
            previous_gap = self._previous_ma_gap.get(symbol)
            crossed_below_ma = previous_gap is not None and previous_gap >= 0 > current_gap
            self._previous_ma_gap[symbol] = current_gap

        if current_price <= cost_price * (1 - self.stop_loss):
            return {"matched_conditions": ["止损：跌幅达到设定阈值"], "signal_price": round(current_price, 6)}
        if crossed_below_ma and dynamic_ma is not None:
            return {"matched_conditions": [f"跌破动态 MA{self.ma_window}"], "signal_price": round(current_price, 6), "dynamic_ma": round(dynamic_ma, 6)}
        return None
