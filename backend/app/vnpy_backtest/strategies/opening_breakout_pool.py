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
        self.params = dict(params)
        self.buy_start = time.fromisoformat(str(self.params.get("scan_start_time", self.params.get("buy_start", "09:30"))))
        self.buy_end = time.fromisoformat(str(self.params.get("scan_end_time", self.params.get("buy_end", "10:00"))))
        self.stop_loss = abs(float(self.params.get("stop_loss_pct", self.params.get("stop_loss", 0.02))))
        self.take_profit = self._positive_param("take_profit_pct")
        self.trailing_stop = self._positive_param("trailing_stop_pct")
        self.trailing_take_profit_activate = self._positive_param("trailing_take_profit_activate_pct")
        self.trailing_take_profit_drawdown = self._positive_param("trailing_take_profit_drawdown_pct")
        self.max_hold_days = self._integer_param("max_hold_days")
        self.ma_window = int(self.params.get("ma_exit_period", self.params.get("ma_window", 5)))
        self._session_date: date | None = None
        self._today_cumulative_volume: dict[str, float] = defaultdict(float)
        self._today_cumulative_amount: dict[str, float] = defaultdict(float)
        self._today_high: dict[str, float] = {}

    def on_minute(
        self,
        bars: Mapping[str, BarData],
        context: PortfolioContext,
    ) -> Sequence[OrderIntent]:
        if self._session_date != context.timestamp.date():
            self._session_date = context.timestamp.date()
            self._today_cumulative_volume.clear()
            self._today_cumulative_amount.clear()
            self._today_high.clear()

        for symbol, bar in bars.items():
            high_price = float(bar.high_price)
            if high_price > 0:
                self._today_high[symbol] = max(self._today_high.get(symbol, 0.0), high_price)

        if context.timestamp.time() <= self.buy_end:
            for symbol, bar in bars.items():
                self._today_cumulative_volume[symbol] += max(float(bar.volume), 0.0)
                self._today_cumulative_amount[symbol] += max(float(bar.turnover), 0.0)

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
                position.high_water_price,
                position.entry_trading_day_index,
                context.trading_day_index,
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
        if not self._passes_basic_filter(symbol, bar, context):
            return None
        reference = context.daily_references.get(symbol)
        price = float(bar.close_price)
        if reference is None or price <= 0 or reference.previous_close is None:
            return None
        previous_same_time_volume = reference.previous_cumulative_volumes.get(bar.datetime.time())
        current_volume = self._today_cumulative_volume[symbol]
        if previous_same_time_volume is None or previous_same_time_volume <= 0:
            return None
        previous_gain = self._previous_day_gain(reference)
        pre_previous_gain = self._pre_previous_day_gain(reference)
        volume_ratio = current_volume / float(previous_same_time_volume)
        today_gain = price / float(reference.previous_close) - 1
        conditions = self._entry_conditions(
            symbol=symbol,
            reference=reference,
            volume_ratio=volume_ratio,
            previous_gain=previous_gain,
            pre_previous_gain=pre_previous_gain,
            today_gain=today_gain,
        )
        if not conditions:
            return None
        return {
            "matched_conditions": [self._condition_label(item) for item in conditions],
            "matched_condition_ids": conditions,
            "primary_reason": conditions[0],
            "signal_price": round(price, 6),
            "volume_ratio": volume_ratio,
            "today_return": today_gain,
            "previous_return": previous_gain,
        }

    def _entry_conditions(
        self,
        *,
        symbol: str,
        reference,
        volume_ratio: float,
        previous_gain: float | None,
        pre_previous_gain: float | None,
        today_gain: float,
    ) -> list[str]:
        has_volume_surge = volume_ratio >= self.VOLUME_MULTIPLE
        condition_1 = bool(
            reference.previous_high is not None
            and reference.previous_open is not None
            and reference.previous_close < reference.previous_open
            and has_volume_surge
            and self._today_high.get(symbol, 0.0) > float(reference.previous_high)
        )
        condition_2 = bool(
            pre_previous_gain is not None
            and previous_gain is not None
            and today_gain > 0.03
            and previous_gain < 0.05
            and pre_previous_gain < 0.05
            and has_volume_surge
        )
        condition_3 = bool(previous_gain is not None and 0 < previous_gain < 0.03 and has_volume_surge)
        return [
            condition
            for condition, passed in (
                ("condition_1", condition_1),
                ("condition_2", condition_2),
                ("condition_3", condition_3),
            )
            if passed
        ]

    @staticmethod
    def _condition_label(condition: str) -> str:
        return {
            "condition_1": "条件 1：前日阴线、同期累计量达 1.5 倍且盘中突破昨日高点",
            "condition_2": "条件 2：当日涨幅大于 3%、前两日涨幅小于 5%且同期累计量达 1.5 倍",
            "condition_3": "条件 3：前日上涨小于 3%且同期累计量达 1.5 倍",
        }[condition]

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

    def _passes_basic_filter(self, symbol: str, bar: BarData, context: PortfolioContext) -> bool:
        basic_filter = self.params.get("basic_filter")
        if not isinstance(basic_filter, Mapping) or not basic_filter.get("enabled", True):
            return True
        price = float(bar.close_price)
        if basic_filter.get("price_min") is not None and price < float(basic_filter["price_min"]):
            return False
        if basic_filter.get("price_max") is not None and price > float(basic_filter["price_max"]):
            return False
        amount = self._today_cumulative_amount[symbol]
        if basic_filter.get("amount_min") is not None and amount < float(basic_filter["amount_min"]):
            return False
        if basic_filter.get("amount_max") is not None and amount > float(basic_filter["amount_max"]):
            return False
        name = context.instrument_names.get(symbol, "")
        if basic_filter.get("exclude_st") and ("ST" in name.upper() or "退" in name):
            return False
        boards = basic_filter.get("boards")
        return not isinstance(boards, list) or not boards or self._symbol_board(symbol) in boards

    @staticmethod
    def _symbol_board(symbol: str) -> str:
        code, _, exchange = symbol.partition(".")
        if exchange == "BJ":
            return "北交所"
        if code.startswith(("300", "301")):
            return "创业板"
        if code.startswith(("688", "689")):
            return "科创板"
        return "沪主板" if exchange == "SH" else "深主板"

    def _positive_param(self, name: str) -> float | None:
        value = self.params.get(name)
        if value is None:
            return None
        return abs(float(value))

    def _integer_param(self, name: str) -> int | None:
        value = self.params.get(name)
        if value is None:
            return None
        return max(int(value), 1)

    def _sell_diagnostic(
        self,
        symbol: str,
        current_price: float,
        cost_price: float,
        high_water_price: float | None,
        entry_trading_day_index: int | None,
        trading_day_index: int,
        reference,
    ) -> dict[str, object] | None:
        if current_price <= 0 or cost_price <= 0:
            return None
        dynamic_ma = None
        if reference is not None and self.ma_window > 1 and len(reference.closes) >= self.ma_window - 1:
            # Dynamic MA5: four completed daily closes plus this minute's close.
            dynamic_ma = (sum(reference.closes[-(self.ma_window - 1):]) + current_price) / self.ma_window
        if self.stop_loss > 0 and current_price <= cost_price * (1 - self.stop_loss):
            return {"matched_conditions": ["止损：跌幅达到设定阈值"], "signal_price": round(current_price, 6)}
        if (
            self.max_hold_days is not None
            and entry_trading_day_index is not None
            and trading_day_index - entry_trading_day_index >= self.max_hold_days
        ):
            return {"matched_conditions": ["达到最长持仓交易日"], "signal_price": round(current_price, 6)}
        if self.take_profit is not None and current_price >= cost_price * (1 + self.take_profit):
            return {"matched_conditions": ["止盈：涨幅达到设定阈值"], "signal_price": round(current_price, 6)}
        if high_water_price is not None and self.trailing_stop is not None and current_price <= high_water_price * (1 - self.trailing_stop):
            return {"matched_conditions": ["移动止损：从持仓高点回撤达到设定阈值"], "signal_price": round(current_price, 6), "high_water_price": round(high_water_price, 6)}
        if (
            high_water_price is not None
            and self.trailing_take_profit_activate is not None
            and self.trailing_take_profit_drawdown is not None
            and high_water_price >= cost_price * (1 + self.trailing_take_profit_activate)
            and current_price <= high_water_price * (1 - self.trailing_take_profit_drawdown)
        ):
            return {"matched_conditions": ["回撤止盈：达到启动收益后从持仓高点回撤"], "signal_price": round(current_price, 6), "high_water_price": round(high_water_price, 6)}
        if dynamic_ma is not None and current_price < dynamic_ma:
            return {"matched_conditions": [f"跌破动态 MA{self.ma_window}"], "signal_price": round(current_price, 6), "dynamic_ma": round(dynamic_ma, 6)}
        return None
