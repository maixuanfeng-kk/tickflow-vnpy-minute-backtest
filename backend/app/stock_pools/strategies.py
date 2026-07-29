"""Built-in research stock-pool strategies."""
from __future__ import annotations

from datetime import date
import re

import polars as pl

from app.stock_pools.base import StockPoolInput


class MonthlyGrowthTrendStrategy:
    """Monthly pool using disclosed financial growth and daily trend signals."""

    def __init__(self, params: dict[str, object]) -> None:
        self.revenue_yoy_min = float(params.get("revenue_yoy_min", 0.15))
        self.ma_window = int(params.get("ma_window", 60))
        self.ma_days = int(params.get("ma_days", 5))
        self.high_window = int(params.get("high_window", 200))
        self.high_days = int(params.get("high_days", 20))
        self.listing_days_min = int(params.get("listing_days_min", 250))
        self.market_cap_min = float(params.get("market_cap_min", 10_000_000_000))

    def build(self, data: StockPoolInput) -> pl.DataFrame:
        financials = self._latest_financials(data.financials, data.as_of_date)
        financial_by_symbol = {row["symbol"]: row for row in financials.to_dicts()}
        listing_by_symbol = {
            row["symbol"]: row["listing_date"]
            for row in data.instruments.to_dicts()
            if row.get("listing_date") is not None
        }
        market_dates = sorted(set(data.daily["date"].to_list()))
        rows: list[dict[str, object]] = []
        for symbol_frame in data.daily.partition_by("symbol", maintain_order=False):
            if symbol_frame.height < self.high_window:
                continue
            ordered = symbol_frame.sort("date")
            symbol = str(ordered["symbol"][0])
            # The full-history XBX dataset also contains delisted securities.
            # A candidate must still have a bar at the monthly as-of date.
            if ordered["date"][-1] != data.as_of_date:
                continue
            stock_name = ordered["name"][-1]
            is_st = self._is_st_name(stock_name)
            if is_st:
                continue
            listing_date = listing_by_symbol.get(symbol)
            listing_trading_days = sum(day >= listing_date for day in market_dates) if listing_date else 0
            if listing_trading_days < self.listing_days_min:
                continue
            closes = [float(value) for value in ordered["close"].to_list()]
            highs = [float(value) for value in ordered["high"].to_list()]
            market_cap = ordered["total_mv"][-1]
            latest = financial_by_symbol.get(symbol)
            if latest is None or market_cap is None:
                continue
            trailing_ma = [sum(closes[index - self.ma_window + 1:index + 1]) / self.ma_window
                           for index in range(self.ma_window - 1, len(closes))]
            recent_close = closes[-self.ma_days:]
            recent_ma = trailing_ma[-self.ma_days:]
            above_ma = len(recent_ma) == self.ma_days and all(price > ma for price, ma in zip(recent_close, recent_ma))
            # A breakout must be strictly above the *previous* 200 sessions.
            # The current bar is deliberately excluded from the reference window:
            # equality with an old high is a retest, not a new high.
            prior_highs = [max(highs[index - self.high_window:index])
                           for index in range(self.high_window, len(highs))]
            trigger_indices = [index for index, prior_high in zip(range(self.high_window, len(highs)), prior_highs)
                               if highs[index] > prior_high]
            recent_trigger_indices = [index for index in trigger_indices if index >= len(highs) - self.high_days]
            revenue_yoy = latest.get("revenue_yoy")
            net_profit = latest.get("net_profit")
            market_cap_passed = float(market_cap) > self.market_cap_min
            condition_1 = bool(market_cap_passed and revenue_yoy is not None and float(revenue_yoy) > self.revenue_yoy_min and above_ma)
            condition_2 = bool(market_cap_passed and net_profit is not None and float(net_profit) > 0 and recent_trigger_indices)
            if not (condition_1 or condition_2):
                continue
            trigger_date = ordered["date"][recent_trigger_indices[-1]].isoformat() if recent_trigger_indices else None
            rows.append({
                "symbol": symbol,
                "pool_month": data.month,
                "as_of_date": data.as_of_date,
                "condition_1": condition_1,
                "condition_2": condition_2,
                "stock_name": stock_name,
                "is_st": is_st,
                "market_cap": float(market_cap),
                "market_cap_passed": market_cap_passed,
                "listing_date": listing_date,
                "listing_trading_days": listing_trading_days,
                "revenue_yoy": float(revenue_yoy) if revenue_yoy is not None else None,
                "net_profit": float(net_profit) if net_profit is not None else None,
                "ma60": round(recent_ma[-1], 6) if recent_ma else None,
                "close": round(closes[-1], 6),
                "high_200": round(prior_highs[-1], 6) if prior_highs else None,
                "high_200_trigger_date": trigger_date,
                "financial_report_date": latest.get("report_date"),
                "financial_publish_date": latest.get("publish_date"),
            })
        schema = {
            "symbol": pl.Utf8, "pool_month": pl.Utf8, "as_of_date": pl.Date,
            "condition_1": pl.Boolean, "condition_2": pl.Boolean,
            "stock_name": pl.Utf8, "is_st": pl.Boolean,
            "market_cap": pl.Float64, "market_cap_passed": pl.Boolean,
            "listing_date": pl.Date, "listing_trading_days": pl.Int64,
            "revenue_yoy": pl.Float64, "net_profit": pl.Float64,
            "ma60": pl.Float64, "close": pl.Float64, "high_200": pl.Float64,
            "high_200_trigger_date": pl.Utf8, "financial_report_date": pl.Date,
            "financial_publish_date": pl.Date,
        }
        return pl.DataFrame(rows, schema=schema).sort("symbol")

    @staticmethod
    def _latest_financials(financials: pl.DataFrame, as_of_date: date) -> pl.DataFrame:
        if financials.is_empty():
            return financials
        eligible = financials.filter(pl.col("publish_date") <= as_of_date)
        if eligible.is_empty():
            return eligible
        return eligible.sort(["symbol", "report_date", "publish_date"]).group_by("symbol", maintain_order=True).tail(1)

    @staticmethod
    def _is_st_name(name: object) -> bool:
        if name is None:
            return True
        normalized = str(name).strip().upper().replace(" ", "")
        return bool(re.match(r"^(?:\*ST|ST|S\*ST|SST)", normalized))
