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
        self.market_cap_min = float(params.get("market_cap_min", 30_000_000_000))
        self.net_profit_min = float(params.get("net_profit_min", 50_000_000))
        self.ma60_basis = "static_as_of"
        self.high_200_basis = "static_as_of_touch"

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
            # The full-history Tushare dataset also contains delisted securities.
            # A candidate must still have a bar at the monthly as-of date.
            if ordered["date"][-1] != data.as_of_date:
                continue
            stock_name = ordered["name"][-1]
            is_st = bool(ordered["is_st"][-1]) if "is_st" in ordered.columns else self._is_st_name(stock_name)
            if is_st:
                continue
            listing_date = listing_by_symbol.get(symbol)
            listing_trading_days = sum(day >= listing_date for day in market_dates) if listing_date else 0
            if listing_trading_days < self.listing_days_min:
                continue
            closes = [float(value) for value in ordered["close"].to_list()]
            opens = [float(value) for value in ordered["open"].to_list()] if "open" in ordered.columns else closes
            highs = [float(value) for value in ordered["high"].to_list()]
            market_cap = ordered["total_mv"][-1]
            latest = financial_by_symbol.get(symbol)
            if latest is None or market_cap is None:
                continue
            static_ma = sum(closes[-self.ma_window:]) / self.ma_window
            recent_open_close = zip(opens[-self.ma_days:], closes[-self.ma_days:], strict=True)
            above_ma = len(opens) >= self.ma_days and all(
                open_price > static_ma or close_price > static_ma
                for open_price, close_price in recent_open_close
            )

            # A touch of the static 200-session high in the recent window
            # qualifies, so a breakout is not discarded after a short pullback.
            static_high_200 = max(highs[-self.high_window:])
            recent_trigger_indices = [
                index
                for index in range(len(highs) - self.high_days, len(highs))
                if highs[index] >= static_high_200
            ]
            revenue_yoy = latest.get("revenue_yoy")
            net_profit = latest.get("net_profit")
            market_cap_passed = float(market_cap) > self.market_cap_min
            condition_1 = bool(market_cap_passed and revenue_yoy is not None and float(revenue_yoy) > self.revenue_yoy_min and above_ma)
            condition_2 = bool(
                market_cap_passed
                and net_profit is not None
                and float(net_profit) > self.net_profit_min
                and recent_trigger_indices
            )
            if not (condition_1 or condition_2):
                continue
            trigger_date = ordered["date"][recent_trigger_indices[-1]].isoformat() if recent_trigger_indices else None
            trigger_price = highs[recent_trigger_indices[-1]] if recent_trigger_indices else None
            high_200_reference = static_high_200 if recent_trigger_indices else None
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
                "ma60_basis": self.ma60_basis,
                "high_200_basis": self.high_200_basis,
                "ma60": round(static_ma, 6),
                "close": round(closes[-1], 6),
                "high_200": round(high_200_reference, 6) if high_200_reference is not None else None,
                "high_200_trigger_date": trigger_date,
                "high_200_trigger_price": round(trigger_price, 6) if trigger_price is not None else None,
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
            "ma60_basis": pl.Utf8, "high_200_basis": pl.Utf8,
            "ma60": pl.Float64, "close": pl.Float64, "high_200": pl.Float64,
            "high_200_trigger_date": pl.Utf8, "high_200_trigger_price": pl.Float64, "financial_report_date": pl.Date,
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
            return False
        normalized = str(name).strip().upper().replace(" ", "")
        return bool(re.match(r"^(?:\*ST|ST|S\*ST|SST)", normalized))
