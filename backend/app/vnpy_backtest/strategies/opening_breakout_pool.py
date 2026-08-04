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
        self._today_high: dict[str, float] = {}

    def on_minute(
        self,
        bars: Mapping[str, BarData],
        context: PortfolioContext,
    ) -> Sequence[OrderIntent]:
        if self._session_date != context.timestamp.date():
            self._session_date = context.timestamp.date()
            self._today_cumulative_volume.clear()
            self._today_high.clear()

        # 条件 1 的“突破昨日高点”一旦在当日盘中发生便持续有效，
        # 不能只在突破发生的那一分钟内成立。
        for symbol, bar in bars.items():
            high_price = float(bar.high_price)
            if high_price > 0:
                self._today_high[symbol] = max(self._today_high.get(symbol, 0.0), high_price)

        if context.timestamp.time() <= self.buy_end:
            for symbol, bar in bars.items():
                self._today_cumulative_volume[symbol] += max(float(bar.volume), 0.0)

        intents: list[OrderIntent] = []
        # Sell checks run through the whole session. The T+1 guard avoids a
        # rejected sell signal on every minute of the purchase day.
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
        pre_previous_gain = self._pre_previous_day_gain(reference)
        today_gain = price / reference.previous_close - 1

        condition_1 = bool(
            reference.previous_high is not None
            and reference.previous_open is not None
            and reference.previous_close < reference.previous_open
            and has_volume_surge
            and self._has_broken_previous_high(symbol, reference.previous_high)
        )
        condition_2 = bool(
            previous_gain is not None
            and pre_previous_gain is not None
            and today_gain > 0.03
            and previous_gain < 0.05
            and pre_previous_gain < 0.05
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
                ("条件2：当日涨幅>3%、昨日及前日涨幅<5%且同期累计量达1.5倍", condition_2),
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

    @staticmethod
    def _pre_previous_day_gain(reference) -> float | None:
        if len(reference.closes) < 3 or reference.closes[-3] <= 0 or reference.closes[-2] <= 0:
            return None
        return reference.closes[-2] / reference.closes[-3] - 1

    def _has_broken_previous_high(self, symbol: str, previous_high: float) -> bool:
        """Return whether today's high has exceeded yesterday's high at any point."""
        return self._today_high.get(symbol, 0.0) > previous_high

    def _sell_diagnostic(
        self,
        symbol: str,
        current_price: float,
        cost_price: float,
        reference,
    ) -> dict[str, object] | None:
        if current_price <= 0 or cost_price <= 0:
            return None
        below_ma = False
        dynamic_ma = None
        if reference is not None and self.ma_window > 1 and len(reference.closes) >= self.ma_window - 1:
            # Dynamic MA5: four completed daily closes plus this minute's close.
            dynamic_ma = (sum(reference.closes[-(self.ma_window - 1):]) + current_price) / self.ma_window
            # Being below the intraday dynamic MA is sufficient; no previous
            # above-MA state or fresh crossing is required.
            below_ma = current_price < dynamic_ma

        if current_price <= cost_price * (1 - self.stop_loss):
            return {"matched_conditions": ["止损：跌幅达到设定阈值"], "signal_price": round(current_price, 6)}
        if below_ma and dynamic_ma is not None:
            return {"matched_conditions": [f"跌破动态 MA{self.ma_window}"], "signal_price": round(current_price, 6), "dynamic_ma": round(dynamic_ma, 6)}
        return None
