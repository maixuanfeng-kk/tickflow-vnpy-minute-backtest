"""Custom cash, fee and trade reporting for the local vn.py run."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData, TradeData


@dataclass
class CompletedTrade:
    entry_datetime: str
    exit_datetime: str
    entry_price: float
    exit_price: float
    volume: float
    pnl: float
    return_pct: float
    commission: float
    stamp_tax: float
    slippage: float


def summarize_trades(
    trades: list[TradeData],
    bars: list[BarData],
    initial_cash: float,
    commission_rate: float,
    stamp_tax_rate: float,
    min_commission: float,
    slippage_rate: float,
) -> dict:
    cash = float(initial_cash)
    position = 0.0
    entry_datetime: datetime | None = None
    position_cost = 0.0
    position_commission = 0.0
    position_slippage = 0.0
    completed: list[CompletedTrade] = []
    total_commission = 0.0
    total_stamp_tax = 0.0
    total_slippage = 0.0

    for trade in sorted(trades, key=lambda item: item.datetime or datetime.min):
        raw_price = float(trade.price)
        is_buy = trade.direction == Direction.LONG
        fill_price = raw_price * (1 + slippage_rate if is_buy else 1 - slippage_rate)
        turnover = fill_price * float(trade.volume)
        commission = max(turnover * commission_rate, min_commission)
        slippage = abs(fill_price - raw_price) * float(trade.volume)
        if is_buy:
            cash -= turnover + commission
            position += trade.volume
            entry_datetime = entry_datetime or trade.datetime
            position_cost += turnover
            position_commission += commission
            position_slippage += slippage
        else:
            cash += turnover - commission - turnover * stamp_tax_rate
            sold_volume = min(float(trade.volume), position)
            allocation = sold_volume / position if position > 0 else 0.0
            if sold_volume > 0 and entry_datetime is not None:
                buy_cost = (position_cost + position_commission) * allocation
                sell_proceeds = turnover - commission - turnover * stamp_tax_rate
                pnl = sell_proceeds - buy_cost
                completed.append(CompletedTrade(
                    entry_datetime=entry_datetime.isoformat(sep=" "),
                    exit_datetime=(trade.datetime or datetime.min).isoformat(sep=" "),
                    entry_price=round(position_cost / position if position else 0.0, 4),
                    exit_price=round(fill_price, 4),
                    volume=sold_volume,
                    pnl=round(pnl, 2),
                    return_pct=round(pnl / buy_cost if buy_cost else 0.0, 6),
                    commission=round(position_commission * allocation + commission, 2),
                    stamp_tax=round(turnover * stamp_tax_rate, 2),
                    slippage=round(position_slippage * allocation + slippage, 2),
                ))
                position_cost *= 1 - allocation
                position_commission *= 1 - allocation
                position_slippage *= 1 - allocation
                position -= sold_volume
                if position <= 0:
                    position = 0.0
                    entry_datetime = None
            else:
                position -= sold_volume
        total_commission += commission
        total_stamp_tax += turnover * stamp_tax_rate if not is_buy else 0.0
        total_slippage += slippage

    last_close = float(bars[-1].close_price) if bars else 0.0
    end_balance = cash + position * last_close
    total_pnl = end_balance - initial_cash
    return {
        "initial_cash": round(initial_cash, 2),
        "end_balance": round(end_balance, 2),
        "total_pnl": round(total_pnl, 2),
        "total_return": round(total_pnl / initial_cash if initial_cash else 0.0, 6),
        "total_commission": round(total_commission, 2),
        "total_stamp_tax": round(total_stamp_tax, 2),
        "total_slippage": round(total_slippage, 2),
        "trade_count": len(completed),
        "open_position": round(position, 2),
        "last_close": last_close,
        "trades": [asdict(item) for item in completed],
    }
