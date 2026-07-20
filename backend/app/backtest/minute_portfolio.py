"""Pure rules for the opening-volume minute portfolio strategy."""
from __future__ import annotations

from typing import TypedDict


VOLUME_RATIO_MIN = 1.5


class Candidate(TypedDict):
    symbol: str
    volume_ratio: float
    today_return: float


def entry_reason(
    *,
    previous_open: float,
    previous_close: float,
    previous_change_pct: float,
    today_return: float,
    volume_ratio: float,
    crossed_previous_high: bool,
) -> str | None:
    """Return the first configured entry branch satisfied by a minute bar."""
    if volume_ratio < VOLUME_RATIO_MIN:
        return None
    if previous_close < previous_open and crossed_previous_high:
        return "previous_bearish_breakout"
    if 0.03 < today_return < 0.05 and previous_change_pct < 0.05:
        return "two_day_moderate_rise"
    if previous_close > previous_open and previous_change_pct < 0.05:
        return "previous_moderate_rise"
    return None


def rank_candidates(rows: list[Candidate]) -> list[Candidate]:
    """Use the agreed deterministic cross-symbol candidate ordering."""
    return sorted(
        rows,
        key=lambda row: (-row["volume_ratio"], -row["today_return"], row["symbol"]),
    )
