"""Shared, dependency-free rules for opening-volume portfolio backtests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from math import isfinite
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class AShareTradingRule:
    board: str
    lot_size: int
    first_buy_minimum: int
    price_limit_pct: float


@dataclass(frozen=True)
class EntryDecision:
    primary_reason: str
    matched_reasons: tuple[str, ...]


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


def evaluate_opening_volume_entry(
    *,
    previous_open: float,
    previous_close: float,
    previous_high: float,
    previous_change_pct: float,
    today_close: float,
    minute_high: float,
    today_return: float | None = None,
    volume_ratio: float,
    params: Mapping[str, Any],
) -> EntryDecision | None:
    """Evaluate the configurable A/B/C opening-volume branches once."""
    def value(name: str, default: Any) -> Any:
        return params.get(name, default)

    def passes_volume(branch: str) -> bool:
        required = value(f"{branch}_volume_multiple", None)
        if required is None:
            required = value("volume_multiple", None)
        return volume_ratio >= float(required if required is not None else 1.5)

    def matches_candle(direction: str) -> bool:
        if direction == "any":
            return True
        if direction == "bearish":
            return previous_close < previous_open
        return previous_close > previous_open

    resolved_today_return = (
        today_return
        if today_return is not None
        else (today_close / previous_close - 1 if previous_close > 0 else 0.0)
    )
    matched: list[str] = []
    if (
        bool(value("enable_branch_a", True))
        and passes_volume("branch_a")
        and matches_candle(str(value("branch_a_previous_candle", "bearish")))
        and minute_high > previous_high
    ):
        matched.append("previous_bearish_breakout")
    if (
        bool(value("enable_branch_b", True))
        and passes_volume("branch_b")
        and float(value("branch_b_today_return_min", 0.03)) < resolved_today_return
        < float(value("branch_b_today_return_max", 0.05))
        and previous_change_pct < float(value("branch_b_previous_return_max", 0.05))
    ):
        matched.append("two_day_moderate_rise")
    if (
        bool(value("enable_branch_c", True))
        and passes_volume("branch_c")
        and matches_candle(str(value("branch_c_previous_candle", "bullish")))
        and previous_change_pct < float(value("branch_c_previous_return_max", 0.05))
    ):
        matched.append("previous_moderate_rise")
    if not matched:
        return None
    return EntryDecision(primary_reason=matched[0], matched_reasons=tuple(matched))


def rank_opening_volume_candidates(
    rows: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    weights: Mapping[str, float] | None = None,
    score_min: float | None = None,
    score_max: float | None = None,
    watchlist_order: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return a deterministic opening-volume candidate order for one minute."""
    candidates = [dict(row) for row in rows]
    if mode == "volume_ratio":
        return sorted(candidates, key=lambda row: (-float(row.get("volume_ratio") or 0), -float(row.get("today_return") or 0), str(row["symbol"])))
    if mode == "watchlist_order":
        order = {symbol: index for index, symbol in enumerate(watchlist_order or ())}
        fallback = len(order)
        return sorted(candidates, key=lambda row: (order.get(str(row["symbol"]), fallback), str(row["symbol"])))
    if mode != "score":
        raise ValueError("candidate_sort must be score, volume_ratio, or watchlist_order")

    active_weights = {
        key: float(weight)
        for key, weight in (weights or {}).items()
        if key in {"volume_ratio", "today_return", "previous_return"}
        and isfinite(float(weight)) and float(weight) > 0
    }
    if not active_weights:
        return rank_opening_volume_candidates(candidates, mode="volume_ratio")
    ranges = {
        key: (min(float(row.get(key) or 0) for row in candidates), max(float(row.get(key) or 0) for row in candidates))
        for key in active_weights
    }
    total_weight = sum(active_weights.values())
    scored: list[dict[str, Any]] = []
    for row in candidates:
        score = 0.0
        for key, weight in active_weights.items():
            low, high = ranges[key]
            value = float(row.get(key) or 0)
            score += ((value - low) / (high - low) * 100 if high > low else 0.0) * weight / total_weight
        row["score"] = score
        if (score_min is None or score >= float(score_min)) and (score_max is None or score <= float(score_max)):
            scored.append(row)
    return sorted(scored, key=lambda row: (-float(row["score"]), -float(row.get("volume_ratio") or 0), -float(row.get("today_return") or 0), str(row["symbol"])))
