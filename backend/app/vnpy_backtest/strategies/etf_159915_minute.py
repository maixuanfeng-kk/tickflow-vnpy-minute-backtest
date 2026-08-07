"""Single-symbol implementation of the 159915 minute trading rules."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, time

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import DailyReference, OrderIntent, PortfolioContext


SYMBOL = "159915.SZ"
MORNING_END = time(11, 29)
TAIL_START = time(14, 45)


class Etf159915MinuteStrategy:
    """Rule state machine for the 159915 ETF.

    The portfolio engine owns execution. This class only observes completed bars
    and returns intents for the next available minute open.
    """

    def __init__(self, params: Mapping[str, object]) -> None:
        self._day: date | None = None
        self._open = 0.0
        self._high = 0.0
        self._cum_volume = 0.0
        self._high_0931 = 0.0
        self._high_0932 = 0.0
        self._reference: DailyReference | None = None
        self._buy_sent = False
        self._sold_today = False
        self._sold_by_54 = False
        self._rebuy_sent = False
        self._special_level = 0
        self._special_trigger = 0.0
        self._special_days_left = 0
        self._protection_level = 0
        self._protection_trigger = 0.0
        self._protection_days_left = 0

    def on_minute(
        self,
        bars: Mapping[str, BarData],
        context: PortfolioContext,
    ) -> Sequence[OrderIntent]:
        if set(bars) != {SYMBOL}:
            raise ValueError("etf_159915_minute accepts only 159915.SZ")
        bar = bars[SYMBOL]
        self._start_day(context.timestamp.date(), context.daily_references.get(SYMBOL))
        self._update_intraday(bar)
        current = float(bar.close_price)
        current_time = context.timestamp.time()

        if context.positions:
            return self._sell_intent(current, current_time)
        if self._sold_today:
            if self._sold_by_54 and not self._rebuy_sent and self._open > 0 and current > self._open:
                self._rebuy_sent = True
                return [self._intent(Direction.LONG, "5.4_rebuy", ["5.4_rebuy"])]
            return []
        if self._buy_sent:
            return []
        return self._buy_intent(current, current_time)

    @staticmethod
    def _strictly_below(value: float, reference: float) -> bool:
        return value < reference

    def _start_day(self, trading_day: date, reference: DailyReference | None) -> None:
        if self._day == trading_day:
            return
        if self._day is not None and self._special_days_left > 0:
            self._special_days_left -= 1
        if self._day is not None and self._protection_days_left > 0:
            self._protection_days_left -= 1
        self._day = trading_day
        self._reference = reference
        self._open = 0.0
        self._high = 0.0
        self._cum_volume = 0.0
        self._high_0931 = 0.0
        self._high_0932 = 0.0
        self._buy_sent = False
        self._sold_today = False
        self._sold_by_54 = False
        self._rebuy_sent = False
        self._refresh_special_state(reference)
        self._refresh_protection_state(reference)

    def _update_intraday(self, bar: BarData) -> None:
        if self._open <= 0:
            self._open = float(bar.open_price)
        self._high = max(self._high, float(bar.high_price))
        self._cum_volume += float(bar.volume)
        current_time = bar.datetime.time()
        if current_time <= time(9, 31):
            self._high_0931 = max(self._high_0931, float(bar.high_price))
        if current_time <= time(9, 32):
            self._high_0932 = max(self._high_0932, float(bar.high_price))

    def _refresh_special_state(self, ref: DailyReference | None) -> None:
        if ref is None or not self._has_previous(ref, 3):
            if self._special_days_left <= 0:
                self._special_level = 0
            return
        c1, c2, c3 = ref.closes[-1], ref.closes[-2], ref.closes[-3]
        if min(c1, c2, c3) <= 0 or ref.previous_open is None:
            return
        ma5_c1 = self._dynamic_ma(ref.closes, 5, 0)
        ma10_c1 = self._dynamic_ma(ref.closes, 10, 0)
        ma5_c2 = self._dynamic_ma(ref.closes, 5, 1)
        ma10_c2 = self._dynamic_ma(ref.closes, 10, 1)
        common = (
            c1 < ma5_c1 and c1 < ma10_c1 and c2 < ma5_c2 and c2 < ma10_c2
            and c1 < ref.previous_open
            and (c2 - c1) / c2 > 0.015
        )
        if not common:
            return
        if (c3 - c1) / c3 > 0.03:
            self._special_level = 1
            self._special_trigger = c2
            self._special_days_left = 2
        else:
            self._special_level = 2
            self._special_trigger = ref.previous_open
            self._special_days_left = max(self._special_days_left, 1)

    def _refresh_protection_state(self, ref: DailyReference | None) -> None:
        if ref is None or not self._has_previous(ref, 2):
            return
        c1, c2 = ref.closes[-1], ref.closes[-2]
        if ref.previous_open is None or ref.previous_low is None or c2 <= 0:
            return
        entity_1 = (c1 - ref.previous_open) / ref.previous_open
        open_2 = self._previous_open(ref, 1)
        entity_2 = (c2 - open_2) / open_2 if open_2 else 0.0
        if entity_2 > 0.013 and entity_1 > 0.013:
            self._protection_level = 3
            self._protection_trigger = c2
            self._protection_days_left = 4
        elif self._protection_level < 3 and (entity_1 > 0.013 or self._signed_change(ref) > 0.013):
            self._protection_level = 2
            self._protection_trigger = ref.previous_low * 1.001
            self._protection_days_left = 2
        elif self._protection_days_left <= 0:
            self._protection_level = 0
            self._protection_trigger = 0.0

    def _buy_intent(self, current: float, current_time: time) -> list[OrderIntent]:
        ref = self._reference
        if ref is None or self._open <= 0:
            return []
        if self._special_level == 1 and self._special_days_left > 0:
            if current_time <= MORNING_END and current > self._special_trigger:
                return self._mark_buy("4.2.3-1")
            return []
        if self._special_level == 2 and self._special_days_left > 0:
            if current_time <= MORNING_END and current > self._special_trigger and (current - self._open) / self._open > 0.005:
                return self._mark_buy("4.2.3-2")
            return []

        prev_close = self._previous_close(ref)
        if prev_close is None or ref.previous_open is None or ref.previous_high is None:
            return []
        if time(9, 31) <= current_time <= MORNING_END:
            if self._is_bullish(ref):
                gap = (ref.previous_high - prev_close) / prev_close
                close_gain = self._signed_change(ref)
                if gap - close_gain > 0.003 and current > ref.previous_high and current > self._high_0931:
                    return self._mark_buy("4.1.1")
                if gap - close_gain <= 0.003:
                    same_time = ref.previous_cumulative_volumes.get(current_time, 0.0)
                    if (current - self._open) / self._open > 0.003 and same_time > 0 and self._cum_volume > 1.2 * same_time and current > self._high_0931:
                        return self._mark_buy("4.1.2")
            else:
                open_change = (ref.previous_open - prev_close) / prev_close
                close_change = self._signed_change(ref)
                if 0.005 < open_change - close_change < 0.015 and current > ref.previous_open and current > self._high_0931:
                    return self._mark_buy("4.2.1")
                if 0 < open_change - close_change < 0.005 and current_time >= time(9, 32) and (current - self._open) / self._open > 0.005 and current > self._high_0932:
                    return self._mark_buy("4.2.2")

        if TAIL_START <= current_time <= time(14, 59):
            return self._tail_buy(ref, current)
        return []

    def _tail_buy(self, ref: DailyReference, current: float) -> list[OrderIntent]:
        entity = self._entity_change(ref)
        signed = self._signed_change(ref)
        if entity < -0.005 and entity > -0.01 and (current - self._open) / self._open >= 0.005 and (current - self._open) / self._open > -entity:
            return self._mark_buy("tail_1")
        if entity <= -0.01 and (current - self._open) / self._open >= -entity - 0.002:
            return self._mark_buy("tail_2")
        if -0.01 < entity < 0.015 and (current - self._open) / self._open > 0.005 and (current - self._open) / self._open > abs(signed) and signed < 0.02:
            return self._mark_buy("tail_3")
        return []

    def _sell_intent(self, current: float, current_time: time) -> list[OrderIntent]:
        ref = self._reference
        if ref is None or self._open <= 0:
            return []
        if (self._open - current) / self._open > 0.01 and self._open > self._previous_close(ref) * 1.02:
            self._sold_today = True
            self._sold_by_54 = True
            return self._intent(Direction.SHORT, "5.4", ["5.4"])
        if self._protection_level == 3 and self._protection_days_left > 0 and current < self._protection_trigger:
            return self._mark_sell("5.3")
        if self._protection_level == 2 and self._protection_days_left > 0 and current < self._protection_trigger:
            return self._mark_sell("5.2")
        if current_time <= time(14, 54):
            prev_change = self._signed_change(ref)
            low_to_close = (self._previous_close(ref) - ref.previous_low) / ref.previous_low if ref.previous_low else 0.0
            if 0.005 < prev_change < 0.013 and current < self._previous_close(ref, 1):
                return self._mark_sell("5.1.1")
            if 0 < prev_change < 0.005 and 0 < low_to_close < 0.013 and current < ref.previous_low:
                return self._mark_sell("5.1.2")
            if 0 < prev_change < 0.005 and low_to_close > 0.013 and current < self._previous_close(ref) - (self._previous_close(ref) - ref.previous_low) * 2 / 3:
                return self._mark_sell("5.1.3")
        if current_time >= time(14, 30) and (self._open - current) / self._open > 0.01 and (self._open - current) / self._open > self._entity_change(ref) and current < self._previous_close(ref) and self._signed_change(ref) > 0:
            return self._mark_sell("5.5")
        if current < min(ref.previous_low or current, self._previous_low(ref) or current) or self._drop_from_previous_close(ref, current) > 0.01:
            return self._mark_sell("5.6")
        return []

    def _mark_buy(self, condition: str) -> list[OrderIntent]:
        self._buy_sent = True
        return [self._intent(Direction.LONG, condition, [condition])]

    def _mark_sell(self, condition: str) -> list[OrderIntent]:
        self._sold_today = True
        return [self._intent(Direction.SHORT, condition, [condition])]

    @staticmethod
    def _intent(direction: Direction, reason: str, conditions: list[str]) -> OrderIntent:
        return OrderIntent(SYMBOL, direction, reason, diagnostic={"matched_conditions": conditions})

    @staticmethod
    def _has_previous(ref: DailyReference, count: int) -> bool:
        return len(ref.closes) >= count

    @staticmethod
    def _dynamic_ma(closes: tuple[float, ...], window: int, offset: int) -> float:
        values = closes[: len(closes) - offset]
        return sum(values[-window:]) / min(len(values), window)

    @staticmethod
    def _previous_close(ref: DailyReference, offset: int = 0) -> float | None:
        if offset == 0:
            return ref.previous_close
        if len(ref.closes) <= offset:
            return None
        return ref.closes[-1 - offset]

    @staticmethod
    def _previous_open(ref: DailyReference, offset: int = 0) -> float:
        if offset == 0:
            return float(ref.previous_open or 0.0)
        if len(ref.opens) <= offset:
            return 0.0
        return float(ref.opens[-1 - offset])

    def _previous_low(self, ref: DailyReference) -> float | None:
        return ref.previous_low

    def _signed_change(self, ref: DailyReference) -> float:
        c1 = self._previous_close(ref)
        c2 = self._previous_close(ref, 1)
        return (c1 - c2) / c2 if c1 is not None and c2 else 0.0

    def _entity_change(self, ref: DailyReference) -> float:
        c1 = self._previous_close(ref)
        return (c1 - ref.previous_open) / ref.previous_open if c1 is not None and ref.previous_open else 0.0

    def _drop_from_previous_close(self, ref: DailyReference, current: float) -> float:
        previous = self._previous_close(ref)
        return (previous - current) / previous if previous else 0.0

    def _is_bullish(self, ref: DailyReference) -> bool:
        return bool(ref.previous_close is not None and ref.previous_open is not None and ref.previous_close > ref.previous_open)
