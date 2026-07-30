"""Compatibility exports for the shared A-share execution rules."""
from app.backtest.opening_volume_shared import AShareTradingRule, rule_for_symbol

__all__ = ["AShareTradingRule", "rule_for_symbol"]
