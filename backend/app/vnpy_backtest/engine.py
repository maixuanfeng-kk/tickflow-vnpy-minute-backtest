"""vn.py engine adapter for local CSV and next-bar-open execution."""
from __future__ import annotations

from math import floor

from vnpy.trader.constant import Direction, Status
from vnpy.trader.object import TradeData
from vnpy_ctastrategy.backtesting import BacktestingEngine

from app.vnpy_backtest.minute_double_ma import buyable_quantity

class LocalNextBarOpenEngine(BacktestingEngine):
    """Fill each pending CTA order once at the next bar open."""

    def __init__(self, max_volume_ratio: float | None = 0.10, lot_size: int = 100) -> None:
        super().__init__()
        self.max_volume_ratio = max_volume_ratio
        self.lot_size = lot_size

    def cross_limit_order(self) -> None:
        bar = self.bar
        for order in list(self.active_limit_orders.values()):
            if order.status == Status.SUBMITTING:
                order.status = Status.NOTTRADED
                self.strategy.on_order(order)

            if bar.open_price <= 0 or bar.volume <= 0:
                order.status = Status.CANCELLED
                self.strategy.on_order(order)
                self.active_limit_orders.pop(order.vt_orderid, None)
                continue

            if self.max_volume_ratio is None:
                fill_volume = float(order.volume)
            else:
                capacity = bar.volume * self.max_volume_ratio
                if order.direction == Direction.LONG:
                    capacity = floor(capacity / self.lot_size) * self.lot_size
                fill_volume = min(float(order.volume), float(capacity))
            if order.direction == Direction.LONG and hasattr(self.strategy, "cash"):
                fill_volume = buyable_quantity(
                    float(self.strategy.cash),
                    float(bar.open_price),
                    float(self.strategy.cash_reserve_ratio),
                    float(self.strategy.position_ratio),
                    self.lot_size,
                    float(self.strategy.slippage_rate),
                )
                if self.max_volume_ratio is not None:
                    fill_volume = min(fill_volume, float(capacity))
            if fill_volume < self.lot_size:
                order.status = Status.CANCELLED
                self.strategy.on_order(order)
                self.active_limit_orders.pop(order.vt_orderid, None)
                continue

            order.traded = fill_volume
            order.status = Status.ALLTRADED
            self.strategy.on_order(order)
            self.active_limit_orders.pop(order.vt_orderid, None)
            self.trade_count += 1
            pos_change = fill_volume if order.direction == Direction.LONG else -fill_volume
            trade = TradeData(
                gateway_name="LOCAL_CSV",
                symbol=order.symbol,
                exchange=order.exchange,
                orderid=order.orderid,
                tradeid=str(self.trade_count),
                direction=order.direction,
                offset=order.offset,
                price=float(bar.open_price),
                volume=fill_volume,
                datetime=bar.datetime,
            )
            self.strategy.pos += pos_change
            self.strategy.on_trade(trade)
            self.trades[trade.vt_tradeid] = trade
