from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.stock_pools.base import StockPoolInput
from app.stock_pools.data import DataReadiness, StockPoolDataAdapter
from app.stock_pools.registry import get_strategy
from app.stock_pools.service import StockPoolService
from app.stock_pools.store import StockPoolStore
from app.stock_pools.strategies import MonthlyGrowthTrendStrategy


def _strategy_input() -> StockPoolInput:
    start = date(2025, 8, 1)
    rows: list[dict[str, object]] = []
    for offset in range(273):
        day = start + timedelta(days=offset)
        rows.append({
            "symbol": "600000.SH", "name": "测试甲", "date": day, "open": 12.0 if offset >= 268 else 10.0,
            "close": 12.0 if offset >= 268 else 10.0,
            "high": 12.0 if offset >= 268 else 10.0, "total_mv": 30_000_000_001.0,
        })
        rows.append({
            "symbol": "000001.SZ", "name": "测试乙", "date": day, "open": 10.0, "close": 10.0,
            "high": 20.0 if offset == 272 else 10.0, "total_mv": 30_000_000_001.0,
        })
    financials = pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ", "600000.SH"],
        "report_date": [date(2025, 3, 31), date(2025, 3, 31), date(2026, 3, 31)],
        "publish_date": [date(2025, 4, 25), date(2025, 4, 25), date(2026, 5, 2)],
        "revenue_yoy": [0.20, 0.0, 0.99],
        "net_profit": [0.0, 60_000_000.0, 9.0],
    })
    return StockPoolInput(
        month="2026-05",
        as_of_date=date(2026, 4, 30),
        daily=pl.DataFrame(rows),
        financials=financials,
        instruments=pl.DataFrame({
            "symbol": ["600000.SH", "000001.SZ"],
            "listing_date": [date(2024, 1, 1), date(2024, 1, 1)],
        }),
    )


def test_monthly_growth_trend_uses_announced_financials_and_union_rules() -> None:
    members = MonthlyGrowthTrendStrategy({}).build(_strategy_input()).sort("symbol")

    assert members["symbol"].to_list() == ["000001.SZ", "600000.SH"]
    evidence = {row["symbol"]: row for row in members.to_dicts()}
    assert evidence["600000.SH"]["condition_1"] is True
    assert evidence["600000.SH"]["condition_2"] is False
    assert evidence["000001.SZ"]["condition_1"] is False
    assert evidence["000001.SZ"]["condition_2"] is True
    assert evidence["000001.SZ"]["market_cap"] == 30_000_000_001.0
    # 2026Q1 was announced after the as-of date, therefore it cannot replace 2025Q1.
    assert evidence["600000.SH"]["financial_publish_date"] == date(2025, 4, 25)


def test_financial_normalisation_prefers_same_period_revenue_growth() -> None:
    source = pl.DataFrame({
        "symbol": ["600000.SH", "600000.SH"],
        "period_end": [date(2025, 3, 31), date(2026, 3, 31)],
        "announce_date": [date(2025, 4, 25), date(2026, 4, 25)],
        "revenue": [100.0, 130.0],
        # This is a quarterly provider field; the screening rule uses the
        # comparable income statement revenue values instead.
        "revenue_yoy": [0.20, 0.01],
        "net_income": [1.0, 2.0],
    })

    normalised = StockPoolDataAdapter._normalise_financials(source)
    latest = normalised.sort("report_date").tail(1).to_dicts()[0]

    assert latest["revenue_yoy"] == pytest.approx(0.30)


def test_point_in_time_name_changes_mark_historical_st_status() -> None:
    adapter = StockPoolDataAdapter(Path("."))
    daily = pl.DataFrame({
        "symbol": ["600777.SH", "600777.SH"],
        "date": [date(2025, 7, 1), date(2026, 4, 30)],
        "name": ["600777.SH", "600777.SH"],
        "is_st": [False, False],
    })
    name_changes = pl.DataFrame({
        "symbol": ["600777.SH"],
        "name": ["ST新潮"],
        "start_date": [date(2024, 4, 30)],
        "end_date": [date(2025, 7, 7)],
    })

    enriched = adapter._apply_point_in_time_name_changes(daily, name_changes)

    assert enriched.filter(pl.col("date") == date(2026, 4, 30))["is_st"].item() is False
    assert enriched.filter(pl.col("date") == date(2025, 7, 1))["is_st"].item() is True


def test_market_cap_must_be_strictly_above_300_billion() -> None:
    source = _strategy_input()
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, source.daily.with_columns(pl.lit(30_000_000_000.0).alias("total_mv")), source.financials, source.instruments)
    )
    assert members.is_empty()


def test_condition_one_accepts_open_above_ma60_when_close_is_not() -> None:
    source = _strategy_input()
    daily = source.daily.with_columns(
        pl.when((pl.col("symbol") == "600000.SH") & (pl.col("date") >= date(2026, 4, 24)))
        .then(pl.lit(12.0)).otherwise(pl.col("open")).alias("open"),
        pl.when((pl.col("symbol") == "600000.SH") & (pl.col("date") >= date(2026, 4, 24)))
        .then(pl.lit(10.0)).otherwise(pl.col("close")).alias("close"),
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    evidence = {row["symbol"]: row for row in members.to_dicts()}
    assert evidence["600000.SH"]["condition_1"] is True


def test_condition_one_requires_all_last_five_days_to_qualify() -> None:
    source = _strategy_input()
    dates = source.daily.filter(pl.col("symbol") == "600000.SH").sort("date")["date"].to_list()
    daily = source.daily.with_columns(
        pl.when((pl.col("symbol") == "600000.SH") & (pl.col("date") >= dates[-5]))
        .then(pl.lit(10.0)).otherwise(pl.col("open")).alias("open"),
        pl.when((pl.col("symbol") == "600000.SH") & (pl.col("date") >= dates[-5]))
        .then(pl.lit(10.0)).otherwise(pl.col("close")).alias("close"),
    ).with_columns(
        pl.when((pl.col("symbol") == "600000.SH") & (pl.col("date") == dates[-1]))
        .then(pl.lit(12.0)).otherwise(pl.col("open")).alias("open"),
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    assert "600000.SH" not in members["symbol"].to_list()


def test_condition_two_does_not_limit_price_when_profit_exceeds_50m() -> None:
    source = _strategy_input()
    daily = source.daily.with_columns(
        pl.when(pl.col("symbol") == "000001.SZ").then(pl.lit(21.0)).otherwise(pl.col("close")).alias("close"),
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    assert "000001.SZ" in members["symbol"].to_list()


def test_condition_two_accepts_a_200_day_high_touched_within_the_last_20_days() -> None:
    source = _strategy_input()
    dates = source.daily.filter(pl.col("symbol") == "000001.SZ").sort("date")["date"].to_list()
    daily = source.daily.with_columns(
        pl.when((pl.col("symbol") == "000001.SZ") & (pl.col("date") == dates[-2]))
        .then(pl.lit(20.0))
        .when((pl.col("symbol") == "000001.SZ") & (pl.col("date") == dates[-1]))
        .then(pl.lit(19.0))
        .otherwise(pl.col("high"))
        .alias("high"),
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    assert "000001.SZ" in members["symbol"].to_list()


def test_static_ma60_requires_one_recent_open_or_close_to_beat_the_as_of_ma() -> None:
    source = _strategy_input()
    members = MonthlyGrowthTrendStrategy({}).build(source)
    assert "600000.SH" in members["symbol"].to_list()

    dates = source.daily.filter(pl.col("symbol") == "600000.SH").sort("date")["date"].to_list()
    daily = source.daily.with_columns(
        pl.when((pl.col("symbol") == "600000.SH") & (pl.col("date") >= dates[-5]))
        .then(pl.lit(10.0))
        .otherwise(pl.col("close"))
        .alias("close"),
        pl.when((pl.col("symbol") == "600000.SH") & (pl.col("date") >= dates[-5]))
        .then(pl.lit(10.0))
        .otherwise(pl.col("open"))
        .alias("open")
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    assert "600000.SH" not in members["symbol"].to_list()


def test_static_200_day_high_accepts_a_recent_touch_but_legacy_does_not() -> None:
    source = _strategy_input()
    dates = source.daily.filter(pl.col("symbol") == "000001.SZ").sort("date")["date"].to_list()
    daily = source.daily.with_columns(
        pl.when((pl.col("symbol") == "000001.SZ") & (pl.col("date") == dates[-51]))
        .then(pl.lit(20.0))
        .otherwise(pl.col("high"))
        .alias("high")
    ).with_columns(
        pl.when((pl.col("symbol") == "000001.SZ") & (pl.col("date") == dates[-1]))
        .then(pl.lit(20.0))
        .otherwise(pl.col("high"))
        .alias("high")
    )
    static_members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    static_evidence = {row["symbol"]: row for row in static_members.to_dicts()}["000001.SZ"]
    assert static_evidence["condition_2"] is True
    assert static_evidence["high_200"] == 20.0
    assert static_evidence["high_200_trigger_date"] == source.as_of_date.isoformat()

def test_strategy_params_default_and_reject_invalid_values() -> None:
    strategy = get_strategy("monthly_growth_trend")
    assert strategy is not None
    assert strategy.version == "5"
    assert strategy.resolve_params({"market_cap_min": 30_000_000_000})["market_cap_min"] == 30_000_000_000
    try:
        strategy.resolve_params({"unknown": 1})
    except ValueError as exc:
        assert "unknown" in str(exc)
    else:
        raise AssertionError("invalid parameter must be rejected")


def test_stock_listed_for_fewer_than_250_trading_days_is_excluded() -> None:
    source = _strategy_input()
    dates = source.daily.filter(pl.col("symbol") == "600000.SH").sort("date")["date"].to_list()
    instruments = source.instruments.with_columns(
        pl.when(pl.col("symbol") == "600000.SH").then(pl.lit(dates[24])).otherwise(pl.col("listing_date")).alias("listing_date")
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, source.daily, source.financials, instruments)
    )
    assert "600000.SH" not in members["symbol"].to_list()


def test_st_name_at_as_of_date_is_excluded() -> None:
    source = _strategy_input()
    daily = source.daily.with_columns(
        pl.when(pl.col("symbol") == "000001.SZ").then(pl.lit("*ST测试")).otherwise(pl.col("name")).alias("name")
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    assert "000001.SZ" not in members["symbol"].to_list()


def test_not_ready_does_not_create_formal_result(tmp_path) -> None:
    adapter = StockPoolDataAdapter(tmp_path)
    readiness = adapter.readiness("2026-05")

    assert readiness.ready is False
    assert any("日K" in item for item in readiness.missing)
    assert not (tmp_path / "pools").exists()


def test_adapter_prefers_professional_daily_dataset_when_present(tmp_path) -> None:
    daily_dir = tmp_path / "kline_daily_pro"
    daily_dir.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["000001.SZ", "000001.SZ"],
        "date": [date(2026, 4, 30), date(2026, 5, 4)],
        "high": [10.0, 11.0], "close": [10.0, 11.0], "total_mv": [10_000_000_001.0, 10_000_000_001.0],
    }).write_parquet(daily_dir / "part.parquet")

    readiness = StockPoolDataAdapter(tmp_path).readiness("2026-05")

    assert readiness.to_dict()["coverage"]["daily_dataset"] == "kline_daily_pro"


def test_adapter_prefers_professional_daily_dataset_when_present(tmp_path) -> None:
    daily_dir = tmp_path / "kline_daily_pro"
    daily_dir.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["000001.SZ", "000001.SZ"],
        "date": [date(2026, 4, 30), date(2026, 5, 4)],
        "high": [10.0, 11.0], "close": [10.0, 11.0], "total_mv": [10_000_000_001.0, 10_000_000_001.0],
    }).write_parquet(daily_dir / "part.parquet")

    readiness = StockPoolDataAdapter(tmp_path).readiness("2026-05")

    assert readiness.to_dict()["coverage"]["daily_dataset"] == "kline_daily_pro"


def test_manual_save_writes_members_and_manifest(tmp_path) -> None:
    class ReadyAdapter:
        def readiness(self, month: str) -> DataReadiness:
            return DataReadiness(month, date(2026, 4, 30), True, (), ("市值条件未启用：缺少历史股本",), 200)

        def load(self, readiness: DataReadiness) -> StockPoolInput:
            return _strategy_input()

    service = StockPoolService(SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)))
    service.adapter = ReadyAdapter()
    service.store = StockPoolStore(tmp_path)

    preview = service.build("monthly_growth_trend", "2026-05")
    assert preview.status == "ready"
    assert preview.member_count == 2
    assert preview.code_string == "000001.SZ,600000.SH"

    saved = service.save("monthly_growth_trend", "2026-05")
    run_dir = tmp_path / "pools" / "research" / "monthly_growth_trend" / "pool_month=2026-05" / f"run_id={saved['run_id']}"
    assert (run_dir / "members.parquet").exists()
    assert (run_dir / "manifest.json").exists()
    manifest = service.store.get_run(saved["run_id"])[0]
    assert saved["member_count"] == 2
    assert manifest["member_count"] == 2
    assert manifest["data_coverage"]["daily_trading_days_before_as_of"] == 200
    assert manifest["params"] == {
        "market_cap_min": 30_000_000_000,
        "revenue_yoy_min": 0.15,
        "net_profit_min": 50_000_000,
    }
    assert saved["generated_pool_key"] == "generated:monthly_growth_trend:2026-05"
    assert (tmp_path / "user_data" / "watchlist_pools" / "generated=monthly_growth_trend--2026-05" / "members.parquet").exists()
    loaded = service.get_run(saved["run_id"])
    assert loaded["member_count"] == 2
    assert loaded["params"] == manifest["params"]
    assert service.list_runs("monthly_growth_trend")[0]["member_count"] == 2
