"""Opening-breakout A-share pool strategy for the local vn.py portfolio runner."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, time
from typing import Mapping, Sequence

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData

from app.backtest.opening_volume_shared import evaluate_opening_volume_entry
from app.vnpy_backtest.strategies.base import OrderIntent, PortfolioContext


class OpeningBreakoutPoolStrategy:
    """Signal-only implementation of the user's opening-breakout rules."""

    def __init__(self, params: Mapping[str, object]) -> None:
        self.params = dict(params)
        self.buy_start = time.fromisoformat(str(self.params.get("scan_start_time", self.params.get("buy_start", "09:30"))))
        self.buy_end = time.fromisoformat(str(self.params.get("scan_end_time", self.params.get("buy_end", "09:59"))))
        self.stop_loss = abs(float(self.params.get("stop_loss_pct", self.params.get("stop_loss", 0.02))))
        self.ma_window = int(self.params.get("ma_exit_period", self.params.get("ma_window", 5)))
        self._session_date: date | None = None
        self._today_cumulative_volume: dict[str, float] = defaultdict(float)

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
        if previous_same_time_volume is None or previous_same_time_volume <= 0:
            return None
        previous_gain = self._previous_day_gain(reference)
        if previous_gain is None or reference.previous_open is None or reference.previous_high is None:
            return None
        decision = evaluate_opening_volume_entry(
            previous_open=float(reference.previous_open),
            previous_close=float(reference.previous_close),
            previous_high=float(reference.previous_high),
            previous_change_pct=previous_gain,
            today_close=price,
            minute_high=float(bar.high_price),
            today_return=price / float(reference.previous_close) - 1,
            volume_ratio=current_volume / float(previous_same_time_volume),
            params=self.params,
        )
        if decision is None:
            return None
        return {
            "matched_conditions": list(decision.matched_reasons),
            "primary_reason": decision.primary_reason,
            "signal_price": round(price, 6),
        }

    @staticmethod
    def _previous_day_gain(reference) -> float | None:
        if len(reference.closes) < 2 or reference.closes[-2] <= 0 or reference.previous_close is None:
            return None
        return reference.previous_close / reference.closes[-2] - 1

    def _sell_diagnostic(
        self,
        symbol: str,
        current_price: float,
        cost_price: float,
        reference,
    ) -> dict[str, object] | None:
        if current_price <= 0 or cost_price <= 0:
            return None
        dynamic_ma = None
        if reference is not None and self.ma_window > 1 and len(reference.closes) >= self.ma_window - 1:
            # Dynamic MA5: four completed daily closes plus this minute's close.
            dynamic_ma = (sum(reference.closes[-(self.ma_window - 1):]) + current_price) / self.ma_window
        if current_price <= cost_price * (1 - self.stop_loss):
            return {"matched_conditions": ["止损：跌幅达到设定阈值"], "signal_price": round(current_price, 6)}
        if dynamic_ma is not None and current_price < dynamic_ma:
            return {"matched_conditions": [f"跌破动态 MA{self.ma_window}"], "signal_price": round(current_price, 6), "dynamic_ma": round(dynamic_ma, 6)}
        return None
