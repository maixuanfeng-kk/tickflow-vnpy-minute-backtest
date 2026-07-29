"""Shared, dependency-free rules for opening-volume portfolio backtests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Sequence


@dataclass(frozen=True)
class AShareTradingRule:
    board: str
    lot_size: int
    first_buy_minimum: int
    price_limit_pct: float


def rule_for_symbol(
    symbol: str,
    *,
    name: str = "",
    limit_pct: float | None = None,
) -> AShareTradingRule:
    """Return the shared A-share execution rule for one normalized symbol."""
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
        return AShareTradingRule(
            rule.board,
            rule.lot_size,
            rule.first_buy_minimum,
            float(limit_pct),
        )
    if name and "ST" in name.upper():
        return AShareTradingRule(rule.board, rule.lot_size, rule.first_buy_minimum, 0.10)
    return rule


def next_actual_bar_time(times: Sequence[time], current: time) -> time | None:
    """Return the next later bar in this trading day, if the symbol has one."""
    return next((item for item in times if item > current), None)
