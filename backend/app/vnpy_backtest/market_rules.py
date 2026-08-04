"""A-share board rules used by the strategy-independent portfolio executor."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


MAINBOARD_ST_TEN_PERCENT_EFFECTIVE_DATE = date(2026, 7, 6)


@dataclass(frozen=True)
class AShareTradingRule:
    board: str
    lot_size: int
    first_buy_minimum: int
    price_limit_pct: float
    tick_size: float = 0.01


def _is_risk_warning_name(name: str | None) -> bool:
    normalized = (name or "").strip().upper().replace(" ", "")
    return normalized.startswith(("*ST", "ST", "S*ST", "SST"))


def rule_for_symbol(
    symbol: str,
    *,
    name: str | None = None,
    trading_day: date | None = None,
    limit_pct: float | None = None,
    tick_size: float | None = None,
) -> AShareTradingRule:
    code, _, exchange = symbol.upper().partition(".")
    if exchange == "BJ":
        rule = AShareTradingRule("bse", 100, 100, 0.30)
    elif code.startswith(("300", "301")):
        rule = AShareTradingRule("chinext", 100, 100, 0.20)
    elif code.startswith(("688", "689")):
        rule = AShareTradingRule("star", 100, 200, 0.20)
    else:
        rule = AShareTradingRule("main", 100, 100, 0.10)
    effective_tick = float(tick_size) if tick_size is not None and float(tick_size) > 0 else rule.tick_size
    if limit_pct is not None and limit_pct > 0:
        return AShareTradingRule(
            rule.board, rule.lot_size, rule.first_buy_minimum, float(limit_pct), effective_tick,
        )
    price_limit_pct = rule.price_limit_pct
    if rule.board == "main" and _is_risk_warning_name(name):
        price_limit_pct = (
            0.10
            if trading_day is None or trading_day >= MAINBOARD_ST_TEN_PERCENT_EFFECTIVE_DATE
            else 0.05
        )
    return AShareTradingRule(
        rule.board, rule.lot_size, rule.first_buy_minimum, price_limit_pct, effective_tick,
    )


def _round_to_tick(price: float, tick_size: float) -> float:
    value = Decimal(str(price))
    tick = Decimal(str(tick_size))
    ticks = (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float(ticks * tick)


def price_limit_bounds(previous_close: float, rule: AShareTradingRule) -> tuple[float, float]:
    """Calculate exchange bounds from the raw pre-close and minimum price tick."""
    upper = _round_to_tick(previous_close * (1 + rule.price_limit_pct), rule.tick_size)
    lower = _round_to_tick(previous_close * (1 - rule.price_limit_pct), rule.tick_size)
    return upper, lower
