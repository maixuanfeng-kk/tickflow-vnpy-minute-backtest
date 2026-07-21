from pathlib import Path

from app.strategy.engine import StrategyEngine


BUILTIN_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "builtin"


def test_opening_volume_strategy_is_registered_with_editable_defaults():
    engine = StrategyEngine(strategy_dirs=[BUILTIN_DIR])

    strategy = engine.get("opening_volume_portfolio")

    assert strategy.meta["timeframes"] == ["1m"]
    assert strategy.meta["scanner_backend"] == "opening_volume"
    assert {item["id"]: item["default"] for item in strategy.meta["params"]} == {
        "scan_start_time": "09:30",
        "scan_end_time": "09:59",
        "volume_multiple": 1.5,
        "enable_branch_a": True,
        "enable_branch_b": True,
        "enable_branch_c": True,
        "stop_loss_pct": 0.02,
        "ma_exit_period": 5,
    }
