from __future__ import annotations

from datetime import time
from importlib import import_module


def _shared_module():
    try:
        return import_module("app.backtest.opening_volume_shared")
    except ModuleNotFoundError:
        return None


def test_shared_market_rules_use_ten_percent_st_fallback() -> None:
    shared = _shared_module()

    assert shared is not None
    assert shared.rule_for_symbol("600000.SH", name="*ST 示例").price_limit_pct == 0.10


def test_shared_next_actual_bar_keeps_lunch_and_expires_at_day_end() -> None:
    shared = _shared_module()

    assert shared is not None
    assert shared.next_actual_bar_time(
        [time(9, 30), time(11, 30), time(13, 0)],
        time(11, 30),
    ) == time(13, 0)
    assert shared.next_actual_bar_time([time(9, 30)], time(9, 30)) is None


def test_shared_entry_uses_native_intraminute_high_breakout() -> None:
    shared = _shared_module()

    assert shared is not None
    decision = shared.evaluate_opening_volume_entry(
        previous_open=11.0,
        previous_close=10.0,
        previous_high=11.0,
        previous_change_pct=-0.01,
        today_close=10.9,
        minute_high=11.01,
        volume_ratio=1.5,
        params={"enable_branch_b": False, "enable_branch_c": False},
    )

    assert decision is not None
    assert decision.primary_reason == "previous_bearish_breakout"
    assert decision.matched_reasons == ("previous_bearish_breakout",)


def test_shared_entry_accepts_chinese_bearish_candle_value_from_ui() -> None:
    shared = _shared_module()

    assert shared is not None
    decision = shared.evaluate_opening_volume_entry(
        previous_open=11.0,
        previous_close=10.0,
        previous_high=11.0,
        previous_change_pct=-0.01,
        today_close=10.9,
        minute_high=11.01,
        volume_ratio=1.5,
        params={
            "enable_branch_a": True,
            "branch_a_previous_candle": "阴线",
            "enable_branch_b": False,
            "enable_branch_c": False,
        },
    )

    assert decision is not None
    assert decision.matched_reasons == ("previous_bearish_breakout",)


def test_shared_entry_keeps_strict_three_percent_branch_b_boundary() -> None:
    shared = _shared_module()

    assert shared is not None
    decision = shared.evaluate_opening_volume_entry(
        previous_open=10.0,
        previous_close=10.0,
        previous_high=20.0,
        previous_change_pct=0.04,
        today_close=10.3,
        minute_high=10.3,
        today_return=0.03,
        volume_ratio=1.5,
        params={"enable_branch_a": False, "enable_branch_c": False},
    )

    assert decision is None


def test_shared_candidate_ranking_uses_volume_ratio_then_return() -> None:
    shared = _shared_module()

    assert shared is not None
    rows = shared.rank_opening_volume_candidates(
        [
            {"symbol": "600000.SH", "volume_ratio": 1.5, "today_return": 0.04, "previous_return": -0.01},
            {"symbol": "300001.SZ", "volume_ratio": 2.0, "today_return": 0.03, "previous_return": 0.01},
        ],
        mode="volume_ratio",
    )

    assert [row["symbol"] for row in rows] == ["300001.SZ", "600000.SH"]
