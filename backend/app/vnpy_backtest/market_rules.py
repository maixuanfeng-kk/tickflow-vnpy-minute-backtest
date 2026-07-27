"""A-share board rules used by the strategy-independent portfolio executor."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AShareTradingRule:
    board: str
    lot_size: int
    first_buy_minimum: int
    price_limit_pct: float


def rule_for_symbol(symbol: str, *, name: str | None = None, limit_pct: float | None = None) -> AShareTradingRule:
    code, _, exchange = symbol.upper().partition(".")
    if exchange == "BJ":
        rule = AShareTradingRule("bse", 100, 100, 0.30)
    elif code.startswith(("300", "301")):
        rule = AShareTradingRule("chinext", 100, 100, 0.20)
    elif code.startswith(("688", "689")):
        rule = AShareTradingRule("star", 100, 200, 0.20)
    else:
        rule = AShareTradingRule("main", 100, 100, 0.10)
    if limit_pct is not None and limit_pct > 0:
        return AShareTradingRule(rule.board, rule.lot_size, rule.first_buy_minimum, float(limit_pct))
    if name and "ST" in name.upper():
        return AShareTradingRule(rule.board, rule.lot_size, rule.first_buy_minimum, 0.05)
    return rule
