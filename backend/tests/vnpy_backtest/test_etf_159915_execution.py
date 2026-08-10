from datetime import date
from types import SimpleNamespace

import pytest

from app.vnpy_backtest.service import VnpyMinuteBacktestConfig, VnpyMinuteBacktestService


def _service():
    return VnpyMinuteBacktestService(SimpleNamespace(store=SimpleNamespace(data_dir=None)))


def test_etf_strategy_rejects_non_159915_symbol():
    config = VnpyMinuteBacktestConfig(
        start=date(2026, 7, 1), end=date(2026, 7, 2), symbols=("510300.SH",), strategy_id="etf_159915_minute"
    )
    with pytest.raises(ValueError, match="159915.SZ"):
        _service().run(config)


def test_etf_strategy_forces_raw_single_position_and_three_percent_reserve():
    config = VnpyMinuteBacktestConfig(
        start=date(2026, 7, 1), end=date(2026, 7, 2), symbols=("159915.SZ",), strategy_id="etf_159915_minute",
        signal_price_basis="qfq", max_positions=10, max_volume_ratio=0.1, position_sizing="score_weight",
    )
    settings = _service()._settings(config)
    assert settings["signal_price_basis"] == "raw"
    assert settings["max_positions"] == 1
    assert settings["max_volume_ratio"] is None
    assert settings["cash_reserve_ratio"] == 0.03


def test_etf_strategy_forces_zero_stamp_tax():
    config = VnpyMinuteBacktestConfig(
        start=date(2026, 7, 1), end=date(2026, 7, 2), symbols=("159915.SZ",),
        strategy_id="etf_159915_minute", stamp_tax_pct=0.001,
    )
    assert _service()._settings(config)["stamp_tax_rate"] == 0.0
