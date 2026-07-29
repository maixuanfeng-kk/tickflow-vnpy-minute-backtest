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
