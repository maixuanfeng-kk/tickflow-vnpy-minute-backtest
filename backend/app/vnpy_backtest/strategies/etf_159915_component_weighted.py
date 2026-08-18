"""Replicate ETF minute signals into weighted component orders."""
from __future__ import annotations

from typing import Mapping

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData

from app.vnpy_backtest.strategies.base import OrderIntent, PortfolioContext


class Etf159915ComponentWeightedStrategy:
    def __init__(self, params: Mapping[str, object]) -> None:
        self.events = {str(key): value for key, value in dict(params.get("etf_events", {})).items()}
        self.batches = {str(key): list(value) for key, value in dict(params.get("component_batches", {})).items()}

    def on_minute(self, bars: Mapping[str, BarData], context: PortfolioContext):
        key = context.timestamp.isoformat(sep=" ")
        event = self.events.get(key)
        if not event:
            return []
        direction = str(event.get("direction", "")).lower() if isinstance(event, Mapping) else ""
        if direction == "sell":
            return [
                OrderIntent(symbol, Direction.SHORT, "etf_sell", volume=position.volume, diagnostic={"cancel_pending_buys": True, "etf_signal": key})
                for symbol, position in context.positions.items()
                if position.volume > 0
            ]
        if direction != "buy" or context.positions or context.reserved_cash > 0:
            return []
        return [
            OrderIntent(str(item["symbol"]), Direction.LONG, "etf_component_buy", diagnostic={
                "normalized_weight": float(item["normalized_weight"]),
                "base_weight": float(item["base_weight"]),
                "filter_factors": item.get("factors", {}),
                "etf_signal": key,
            })
            for item in self.batches.get(key, [])
            if float(item.get("normalized_weight", 0)) > 0
        ]
