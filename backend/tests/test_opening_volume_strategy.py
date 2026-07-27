from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import HTTPException

from app.api import strategy as strategy_api
from app.backtest.minute_portfolio import OpeningVolumeScanConfig, OpeningVolumeScanService, OpeningVolumeStrategyParams
from app.strategy import config as strategy_config
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
        "enable_branch_a": True,
        "branch_a_volume_multiple": 1.5,
        "branch_a_previous_candle": "阴线",
        "enable_branch_b": True,
        "branch_b_volume_multiple": 1.5,
        "branch_b_today_return_min": 0.03,
        "branch_b_today_return_max": 0.05,
        "branch_b_previous_return_max": 0.05,
        "enable_branch_c": True,
        "branch_c_volume_multiple": 1.5,
        "branch_c_previous_candle": "阳线",
        "branch_c_previous_return_max": 0.05,
        "stop_loss_pct": 0.02,
        "ma_exit_period": 5,
    }


def test_opening_volume_scan_service_reads_tickflow_watchlist_only(monkeypatch):
    class Repo:
        def __init__(self):
            self.daily_symbols = None
            self.minute_symbols = None

        def get_daily_batch(self, symbols, start, end, columns):
            self.daily_symbols = symbols
            return pl.DataFrame({
                "symbol": ["600000.SH", "600000.SH"],
                "date": [date(2026, 1, 2), date(2026, 1, 5)],
                "open": [11.0, 10.0],
                "high": [10.5, 10.4],
                "close": [10.0, 10.3],
                "ma5": [9.0, 9.1],
            })

        def get_minute_range(self, symbols, start, end, asset_type):
            self.minute_symbols = symbols
            return pl.DataFrame({
                "symbol": ["600000.SH"] * 4,
                "datetime": [
                    datetime(2026, 1, 2, 9, 30), datetime(2026, 1, 2, 9, 31),
                    datetime(2026, 1, 5, 9, 30), datetime(2026, 1, 5, 9, 31),
                ],
                "open": [10.0, 10.0, 10.0, 10.3],
                "high": [10.0, 10.0, 10.6, 10.4],
                "low": [10.0, 10.0, 10.0, 10.2],
                "close": [10.0, 10.0, 10.2, 10.3],
                "volume": [100.0, 100.0, 150.0, 100.0],
            })

    monkeypatch.setattr(
        "app.backtest.minute_portfolio.watchlist.list_symbols",
        lambda: [{"symbol": "600000.SH"}, {"symbol": "510300.SH"}],
    )
    repo = Repo()

    result = OpeningVolumeScanService(repo).run(OpeningVolumeScanConfig(
        as_of=date(2026, 1, 5),
        strategy_params=OpeningVolumeStrategyParams(),
    ))

    assert repo.daily_symbols == ["600000.SH", "510300.SH"]
    assert repo.minute_symbols == ["600000.SH", "510300.SH"]
    assert result["total"] == 1
    assert result["rows"][0]["symbol"] == "600000.SH"
    assert result["rows"][0]["time"] == "09:31"


def test_native_strategy_run_uses_saved_params(monkeypatch, tmp_path):
    captured = {}

    class ScanService:
        def __init__(self, repo):
            self.repo = repo

        def run(self, config):
            captured["config"] = config
            return {"as_of": str(config.as_of), "strategy": "opening_volume_portfolio", "rows": [], "total": 0, "elapsed_ms": 0}

    monkeypatch.setattr(strategy_api, "OpeningVolumeScanService", ScanService)
    strategy_config.save_override(tmp_path, "opening_volume_portfolio", {
        "params": {
            "branch_a_volume_multiple": 2.1,
            "branch_b_today_return_min": 0.015,
        },
    })
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        strategy_engine=StrategyEngine(strategy_dirs=[BUILTIN_DIR]),
    )))

    result = strategy_api.run_strategy(strategy_api.RunRequest(
        strategy_id="opening_volume_portfolio",
        as_of=date(2026, 1, 5),
    ), request)

    assert result["total"] == 0
    assert captured["config"].strategy_params.branch_a_volume_multiple == 2.1
    assert captured["config"].strategy_params.branch_b_today_return_min == 0.015


def test_native_strategy_run_reports_missing_minute_data_as_bad_request(monkeypatch, tmp_path):
    class ScanService:
        def __init__(self, repo):
            self.repo = repo

        def run(self, config):
            raise ValueError("no minute bars in the selected range")

    monkeypatch.setattr(strategy_api, "OpeningVolumeScanService", ScanService)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
        strategy_engine=StrategyEngine(strategy_dirs=[BUILTIN_DIR]),
    )))

    with pytest.raises(HTTPException) as exc_info:
        strategy_api.run_strategy(strategy_api.RunRequest(
            strategy_id="opening_volume_portfolio",
            as_of=date(2026, 1, 5),
        ), request)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "no minute bars in the selected range"
