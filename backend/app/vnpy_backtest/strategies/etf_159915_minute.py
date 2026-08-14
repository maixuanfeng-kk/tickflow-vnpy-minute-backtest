"""Single-symbol implementation of the 159915 minute trading rules."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, time

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import DailyReference, OrderIntent, PortfolioContext


SYMBOL = "159915.SZ"
MORNING_END = time(11, 29)
TAIL_START = time(14, 46)
SELL_END = time(14, 57)


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
        self._special_fresh = False
        self._observed_position: bool | None = None
        self._pending_54_sell = False
        self._pending_53_setup: object = None
        self._consumed_53_setup: object = None

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
        position = context.positions.get(SYMBOL)
        has_position = position is not None
        self._update_position_lifecycle(has_position)
        if current_time >= time(15, 0):
            return []

        if has_position:
            if position.entry_date == context.timestamp.date():
                return []
            return self._sell_intent(current, current_time, position.entry_date)
        if self._sold_today:
            if (
                self._sold_by_54
                and not self._rebuy_sent
                and self._open > 0
                and current > self._open
            ):
                self._rebuy_sent = True
                return [self._intent(Direction.LONG, "5_4", ["5_4"])]
            return []
        if self._buy_sent:
            return []
        return self._buy_intent(current, current_time)

    def _start_day(self, trading_day: date, reference: DailyReference | None) -> None:
        if self._day == trading_day:
            return
        if self._day is not None and self._special_days_left > 0:
            self._special_days_left -= 1
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
        self._pending_54_sell = False
        self._special_fresh = False
        self._refresh_special_state(reference)

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
        if ref is None or len(ref.closes) < 10:
            if self._special_days_left <= 0:
                self._special_level = 0
            return
        c1, c2, c3 = ref.closes[-1], ref.closes[-2], ref.closes[-3]
        if min(c1, c2, c3) <= 0 or ref.previous_open is None:
            return
        ma5_c1 = self._dynamic_ma(ref.closes, 5, 0)
        ma10_c1 = self._dynamic_ma(ref.closes, 10, 0)
        if (
            not (c1 < ma5_c1 and c1 < ma10_c1)
            or (ma5_c1 - c1) / ma5_c1 <= 0.015
            or (c2 - c1) / c2 <= 0.015
        ):
            return
        if (c3 - c1) / c3 > 0.03:
            self._special_level = 1
            self._special_trigger = c2
            self._special_days_left = 2
            self._special_fresh = True
        else:
            if self._special_level == 1 and self._special_days_left > 0:
                return
            self._special_level = 2
            self._special_trigger = ref.previous_open
            self._special_days_left = max(self._special_days_left, 2)
            self._special_fresh = True

    def _buy_intent(self, current: float, current_time: time) -> list[OrderIntent]:
        ref = self._reference
        if ref is None or self._open <= 0:
            return []
        if self._special_level == 1 and self._special_days_left > 0:
            if self._special_buy_time(current_time) and current > self._special_trigger:
                reason = "4_2_3_1" if self._special_fresh else "4_2_3_1_before"
                return self._mark_buy(reason)
            return []
        if self._special_level == 2 and self._special_days_left > 0:
            previous_close = self._previous_close(ref)
            if (
                self._special_buy_time(current_time)
                and previous_close
                and current > self._special_trigger
                and (current - previous_close) / previous_close > 0.005
            ):
                reason = "4_2_3_2" if self._special_fresh else "4_2_3_2_before"
                return self._mark_buy(reason)
            return []

        prev_close = self._previous_close(ref)
        previous_close_2 = self._previous_close(ref, 1)
        if prev_close is None or previous_close_2 is None or ref.previous_open is None or ref.previous_high is None:
            return []
        if time(9, 31) <= current_time <= MORNING_END:
            if self._is_bullish(ref):
                high_gain = (ref.previous_high - previous_close_2) / previous_close_2
                close_gain = (prev_close - previous_close_2) / previous_close_2
                if high_gain - close_gain > 0.003 and current > ref.previous_high and current > self._high_0931:
                    return self._mark_buy("4_1_1")
                if high_gain - close_gain <= 0.003:
                    same_time = ref.previous_cumulative_volumes.get(current_time, 0.0)
                    if (current - self._open) / self._open > 0.003 and same_time > 0 and self._cum_volume > 1.2 * same_time and current > self._high_0931:
                        return self._mark_buy("4_1_2")
            elif self._is_bearish(ref):
                open_change = (ref.previous_open - previous_close_2) / previous_close_2
                close_change = (prev_close - previous_close_2) / previous_close_2
                if 0.005 < open_change - close_change < 0.015 and current > ref.previous_open and current > self._high_0931:
                    return self._mark_buy("4_2_1")
                if (
                    0 < open_change - close_change < 0.005
                    and current_time >= time(9, 32)
                    and (current - self._open) / self._open > 0.005
                    and current > self._high_0932
                ):
                    return self._mark_buy("4_2_2")

        if TAIL_START <= current_time <= time(14, 59):
            return self._tail_buy(ref, current)
        return []

    def _tail_buy(self, ref: DailyReference, current: float) -> list[OrderIntent]:
        entity = self._entity_change(ref)
        previous_close = self._previous_close(ref)
        if entity < -0.005 and entity > -0.01 and (current - self._open) / self._open >= 0.005 and (current - self._open) / self._open > -entity:
            return self._mark_buy("4_3_1")
        if entity <= -0.01 and (current - self._open) / self._open >= -entity - 0.002:
            return self._mark_buy("4_3_2")
        if -0.01 < entity < 0.015 and (current - self._open) / self._open > 0.005 and (current - self._open) / self._open > entity and previous_close is not None and current < previous_close * 1.02:
            return self._mark_buy("4_3_3")
        return []

    def _sell_intent(
        self,
        current: float,
        current_time: time,
        entry_date: date | None,
    ) -> list[OrderIntent]:
        ref = self._reference
        if (
            ref is None
            or self._open <= 0
            or current_time < time(9, 31)
            or current_time > SELL_END
        ):
            return []
        if (self._open - current) / self._open > 0.01 and self._open > self._previous_close(ref) * 1.02:
            self._sold_today = True
            self._pending_54_sell = True
            return [self._intent(Direction.SHORT, "5_4", ["5_4"])]
        protection = self._sell_protection(ref, entry_date)
        if protection is not None:
            trigger, reason, setup = protection
            if current < trigger:
                if reason in {"5_3", "5_3_before_2_3"}:
                    self._pending_53_setup = setup
                return self._mark_sell(reason)
            return []
        if current_time <= time(14, 54):
            prev_change = self._signed_change(ref)
            low_to_close = (self._previous_close(ref) - ref.previous_low) / ref.previous_low if ref.previous_low else 0.0
            if 0.005 < prev_change < 0.013 and current < self._previous_close(ref, 1):
                return self._mark_sell("5_1_1")
            if 0 < prev_change < 0.005 and 0 < low_to_close < 0.013 and current < ref.previous_low:
                return self._mark_sell("5_1_2")
            if 0 < prev_change < 0.005 and low_to_close > 0.013 and current < self._previous_close(ref) - (self._previous_close(ref) - ref.previous_low) * 2 / 3:
                return self._mark_sell("5_1_3")
        if current_time >= time(14, 30) and (self._open - current) / self._open > 0.01 and (self._open - current) / self._open > self._entity_change(ref) and current < self._previous_close(ref) and self._signed_change(ref) > 0:
            return self._mark_sell("5_5")
        previous_close = self._previous_close(ref)
        previous_low = ref.previous_low
        previous_low_2 = self._previous_low(ref, 1)
        if (
            bool(
                previous_low is not None
                and previous_low_2 is not None
                and current < min(previous_low, previous_low_2)
            )
            or bool(previous_close and (previous_close - current) / previous_close > 0.01)
        ):
            return self._mark_sell("5_6")
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
    def _special_buy_time(current_time: time) -> bool:
        return (
            time(9, 31) <= current_time <= time(11, 30)
            or TAIL_START <= current_time <= time(14, 59)
        )

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

    def _previous_low(self, ref: DailyReference, offset: int = 0) -> float | None:
        if offset == 0:
            return ref.previous_low
        if len(ref.lows) <= offset:
            return None
        return ref.lows[-1 - offset]

    def _update_position_lifecycle(self, has_position: bool) -> None:
        if self._observed_position is True and self._pending_54_sell:
            self._sold_by_54 = not has_position
            self._pending_54_sell = False
        if self._observed_position is True and self._pending_53_setup is not None:
            if not has_position:
                self._consumed_53_setup = self._pending_53_setup
            self._pending_53_setup = None
        self._observed_position = has_position

    def _five_two_trigger(self, ref: DailyReference, offset: int) -> float | None:
        close = self._previous_close(ref, offset)
        open_price = self._previous_open(ref, offset)
        low = self._previous_low(ref, offset)
        if close is None or open_price <= 0 or low is None:
            return None
        entity_change = (close - open_price) / open_price
        prior_close = self._previous_close(ref, offset + 1)
        signed_change = (close - prior_close) / prior_close if prior_close else 0.0
        return low * 1.001 if entity_change > 0.013 or signed_change > 0.013 else None

    def _sell_protection(
        self,
        ref: DailyReference,
        entry_date: date | None = None,
    ) -> tuple[float, str, object] | None:
        for older_offset in (1, 2, 3, 4):
            newer_offset = older_offset - 1
            older_close = self._previous_close(ref, older_offset)
            newer_close = self._previous_close(ref, newer_offset)
            older_open = self._previous_open(ref, older_offset)
            newer_open = self._previous_open(ref, newer_offset)
            if (
                older_close is not None
                and newer_close is not None
                and older_open > 0
                and newer_open > 0
                and (older_close - older_open) / older_open > 0.013
                and (newer_close - newer_open) / newer_open > 0.013
            ):
                older_date = self._previous_date(ref, older_offset)
                if entry_date is not None and (
                    older_date is None or entry_date > older_date
                ):
                    continue
                setup = (
                    "5_3",
                    older_open,
                    older_close,
                    newer_open,
                    newer_close,
                )
                if setup == self._consumed_53_setup:
                    return None
                reason = "5_3" if older_offset == 1 else "5_3_before_2_3"
                return older_close, reason, setup

        previous_52 = self._five_two_trigger(ref, 1)
        if previous_52 is not None:
            return previous_52, "5_2_before_2_2", None
        fresh_52 = self._five_two_trigger(ref, 0)
        if fresh_52 is not None:
            return fresh_52, "5_2", None
        return None

    @staticmethod
    def _previous_date(ref: DailyReference, offset: int) -> date | None:
        if len(ref.dates) <= offset:
            return None
        return ref.dates[-1 - offset]

    def _signed_change(self, ref: DailyReference) -> float:
        c1 = self._previous_close(ref)
        c2 = self._previous_close(ref, 1)
        return (c1 - c2) / c2 if c1 is not None and c2 else 0.0

    def _entity_change(self, ref: DailyReference) -> float:
        c1 = self._previous_close(ref)
        return (c1 - ref.previous_open) / ref.previous_open if c1 is not None and ref.previous_open else 0.0

    def _is_bullish(self, ref: DailyReference) -> bool:
        return bool(ref.previous_close is not None and ref.previous_open is not None and ref.previous_close > ref.previous_open)

    def _is_bearish(self, ref: DailyReference) -> bool:
        return bool(ref.previous_close is not None and ref.previous_open is not None and ref.previous_close < ref.previous_open)
