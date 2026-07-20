"""vn.py CTA implementation of the minute double-MA volume strategy."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, time
from typing import ClassVar

import pandas as pd
from vnpy.trader.constant import Direction, Status
from vnpy.trader.object import BarData, TradeData
from vnpy_ctastrategy import CtaTemplate


def moving_average_cross(
    closes: Sequence[float],
    short_window: int,
    long_window: int,
) -> tuple[bool, bool]:
    """Return (golden_cross, death_cross) at the latest completed bar."""
    if short_window <= 0 or long_window <= 0 or short_window > long_window:
        raise ValueError("moving-average windows must be positive and short <= long")
    if len(closes) < long_window + 1:
        return False, False
    close = pd.Series(closes, dtype="float64")
    short_now = close.iloc[-short_window:].mean()
    long_now = close.iloc[-long_window:].mean()
    short_previous = close.iloc[-short_window - 1:-1].mean()
    long_previous = close.iloc[-long_window - 1:-1].mean()
    return (
        short_previous <= long_previous and short_now > long_now,
        short_previous >= long_previous and short_now < long_now,
    )


def amount_confirmed(amounts: Sequence[float], window: int, multiple: float) -> bool:
    """Check current amount against the mean of the preceding bars."""
    if window <= 0 or len(amounts) < window + 1:
        return False
    previous = amounts[-window - 1:-1]
    previous_mean = sum(previous) / len(previous)
    return previous_mean > 0 and amounts[-1] >= previous_mean * multiple


def can_open_new_position(current: time, latest: time) -> bool:
    return current <= latest


def can_sell_t_plus_one(entry_date: date | None, current_date: date) -> bool:
    return entry_date is None or current_date > entry_date


def round_lot(quantity: float, lot_size: int) -> int:
    if lot_size <= 0:
        raise ValueError("lot_size must be positive")
    return max(int(quantity) // lot_size * lot_size, 0)


def buyable_quantity(
    cash: float,
    price: float,
    cash_reserve_ratio: float,
    position_ratio: float,
    lot_size: int,
    slippage_rate: float,
) -> int:
    """Size a new position from the cash remaining after earlier fills."""
    if price <= 0:
        return 0
    budget = cash * (1 - cash_reserve_ratio) * position_ratio
    return round_lot(budget / (price * (1 + slippage_rate)), lot_size)


class MinuteDoubleMaVolumeStrategy(CtaTemplate):
    """5/20-minute golden-cross strategy with amount and A-share controls."""

    author = "TickFlow"

    short_window = 5
    long_window = 20
    amount_window = 20
    amount_multiple = 1.2
    take_profit = 0.02
    stop_loss = 0.015
    latest_buy_time = "14:30"
    force_sell_time = "14:50"
    position_ratio = 1.0
    initial_cash = 100_000.0
    cash_reserve_ratio = 0.03
    default_lot_size = 100
    max_volume_ratio = 0.10
    t_plus_one = True
    commission_rate = 1.2 / 10_000
    stamp_tax_rate = 1 / 1_000
    min_commission = 5.0
    slippage_rate = 1 / 10_000

    parameters: ClassVar[list[str]] = [
        "short_window", "long_window", "amount_window", "amount_multiple",
        "take_profit", "stop_loss", "latest_buy_time", "force_sell_time",
        "position_ratio", "initial_cash", "cash_reserve_ratio", "default_lot_size",
        "max_volume_ratio", "t_plus_one", "commission_rate", "stamp_tax_rate",
        "min_commission", "slippage_rate",
    ]
    variables: ClassVar[list[str]] = ["pos"]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.session_date: date | None = None
        self.session_closes: list[float] = []
        self.session_amounts: list[float] = []
        self.entry_date: date | None = None
        self.entry_price: float = 0.0
        self.pending_orderids: set[str] = set()
        self.cash = float(self.initial_cash)

    def on_init(self) -> None:
        self.write_log("分钟双均线放量策略初始化")

    def on_start(self) -> None:
        self.write_log("分钟双均线放量策略启动")

    def on_bar(self, bar: BarData) -> None:
        bar_date = bar.datetime.date()
        if self.session_date != bar_date:
            self.session_date = bar_date
            self.session_closes.clear()
            self.session_amounts.clear()

        self.session_closes.append(float(bar.close_price))
        self.session_amounts.append(float(bar.turnover))
        if self.pending_orderids:
            return

        current_time = bar.datetime.time()
        if self.pos > 0:
            if self.t_plus_one and not can_sell_t_plus_one(self.entry_date, bar_date):
                return
            _, death_cross = moving_average_cross(
                self.session_closes, self.short_window, self.long_window,
            )
            return_rate = bar.close_price / self.entry_price - 1 if self.entry_price > 0 else 0.0
            price_exit = return_rate >= self.take_profit or return_rate <= -abs(self.stop_loss)
            time_exit = current_time >= time.fromisoformat(str(self.force_sell_time))
            if death_cross or price_exit or time_exit:
                self.pending_orderids.update(self.sell(bar.close_price, self.pos))
            return

        if not can_open_new_position(current_time, time.fromisoformat(str(self.latest_buy_time))):
            return
        golden_cross, _ = moving_average_cross(
            self.session_closes, self.short_window, self.long_window,
        )
        confirmed = amount_confirmed(
            self.session_amounts, self.amount_window, float(self.amount_multiple),
        )
        if not golden_cross or not confirmed or bar.close_price <= 0:
            return

        quantity = buyable_quantity(
            self.cash,
            bar.close_price,
            float(self.cash_reserve_ratio),
            float(self.position_ratio),
            int(self.default_lot_size),
            float(self.slippage_rate),
        )
        if quantity > 0:
            self.pending_orderids.update(self.buy(bar.close_price, quantity))

    def on_order(self, order) -> None:
        if order.status in {Status.ALLTRADED, Status.CANCELLED, Status.REJECTED}:
            self.pending_orderids.discard(order.vt_orderid)

    def on_trade(self, trade: TradeData) -> None:
        raw_price = float(trade.price)
        volume = float(trade.volume)
        if trade.direction == Direction.LONG:
            fill_price = raw_price * (1 + float(self.slippage_rate))
            turnover = fill_price * volume
            commission = max(turnover * float(self.commission_rate), float(self.min_commission))
            self.cash -= turnover + commission
            self.entry_date = trade.datetime.date() if trade.datetime else None
            self.entry_price = (turnover + commission) / volume
        else:
            fill_price = raw_price * (1 - float(self.slippage_rate))
            turnover = fill_price * volume
            commission = max(turnover * float(self.commission_rate), float(self.min_commission))
            self.cash += turnover - commission - turnover * float(self.stamp_tax_rate)
            if self.pos <= 0:
                self.entry_date = None
                self.entry_price = 0.0
