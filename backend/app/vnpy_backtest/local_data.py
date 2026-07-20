"""Convert TickFlow minute-K rows into vn.py BarData objects."""
from __future__ import annotations

import polars as pl
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

EXCHANGE_MAP = {
    "SH": Exchange.SSE,
    "SZ": Exchange.SZSE,
    "BJ": Exchange.BSE,
}


def bars_from_minute_frame(symbol: str, frame: pl.DataFrame) -> list[BarData]:
    """Convert repository minute-K rows for one A-share symbol."""
    exchange_code = symbol.rsplit(".", 1)[-1].upper()
    exchange = EXCHANGE_MAP.get(exchange_code)
    if exchange is None:
        raise ValueError(f"unsupported vn.py exchange: {exchange_code}")
    stock_symbol = symbol.split(".", 1)[0]

    bars: list[BarData] = []
    for row in frame.sort("datetime").iter_rows(named=True):
        bars.append(BarData(
            gateway_name="TICKFLOW",
            symbol=stock_symbol,
            exchange=exchange,
            datetime=row["datetime"],
            interval=Interval.MINUTE,
            volume=float(row["volume"] or 0.0),
            turnover=float(row["amount"] or 0.0),
            open_price=float(row["open"] or 0.0),
            high_price=float(row["high"] or 0.0),
            low_price=float(row["low"] or 0.0),
            close_price=float(row["close"] or 0.0),
        ))
    return bars
