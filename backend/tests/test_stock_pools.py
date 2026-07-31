from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from app.api.stock_pools import StockPoolBuildRequest
from app.stock_pools.base import StockPoolInput
from app.stock_pools.data import DataReadiness, StockPoolDataAdapter
from app.stock_pools.service import StockPoolService
from app.stock_pools.store import StockPoolStore
from app.stock_pools.strategies import MonthlyGrowthTrendStrategy
from app.stock_pools.registry import get_strategy
from app.services.watchlist_pools import WatchlistPoolStore


def _strategy_input() -> StockPoolInput:
    start = date(2025, 8, 1)
    rows: list[dict[str, object]] = []
    for offset in range(273):
        day = start + timedelta(days=offset)
        rows.append({
            "symbol": "600000.SH", "name": "测试甲", "date": day, "close": 12.0 if offset >= 268 else 10.0,
            "high": 12.0 if offset >= 268 else 10.0, "total_mv": 10_000_000_001.0,
        })
        rows.append({
            "symbol": "000001.SZ", "name": "测试乙", "date": day, "close": 10.0,
            "high": 20.0 if offset == 272 else 10.0, "total_mv": 10_000_000_001.0,
        })
    financials = pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ", "600000.SH"],
        "report_date": [date(2025, 3, 31), date(2025, 3, 31), date(2026, 3, 31)],
        "publish_date": [date(2025, 4, 25), date(2025, 4, 25), date(2026, 5, 2)],
        "revenue_yoy": [0.20, 0.0, 0.99],
        "net_profit": [0.0, 1.0, 9.0],
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
    assert evidence["000001.SZ"]["market_cap"] == 10_000_000_001.0
    # 2026Q1 was announced after the as-of date, therefore it cannot replace 2025Q1.
    assert evidence["600000.SH"]["financial_publish_date"] == date(2025, 4, 25)


def test_market_cap_must_be_strictly_above_100_billion() -> None:
    source = _strategy_input()
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, source.daily.with_columns(pl.lit(10_000_000_000.0).alias("total_mv")), source.financials, source.instruments)
    )
    assert members.is_empty()


def test_equal_to_prior_200_day_high_is_not_a_breakout() -> None:
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
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    assert "000001.SZ" not in members["symbol"].to_list()


def test_stock_listed_for_fewer_than_250_trading_days_is_not_excluded() -> None:
    source = _strategy_input()
    dates = source.daily.filter(pl.col("symbol") == "600000.SH").sort("date")["date"].to_list()
    instruments = source.instruments.with_columns(
        pl.when(pl.col("symbol") == "600000.SH").then(pl.lit(dates[24])).otherwise(pl.col("listing_date")).alias("listing_date")
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, source.daily, source.financials, instruments)
    )
    assert "600000.SH" in members["symbol"].to_list()


def test_st_name_at_as_of_date_is_not_excluded() -> None:
    source = _strategy_input()
    daily = source.daily.with_columns(
        pl.when(pl.col("symbol") == "000001.SZ").then(pl.lit("*ST测试")).otherwise(pl.col("name")).alias("name")
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, source.instruments)
    )
    assert "000001.SZ" in members["symbol"].to_list()


def test_not_ready_does_not_create_formal_result(tmp_path) -> None:
    adapter = StockPoolDataAdapter(tmp_path)
    readiness = adapter.readiness("2026-05")

    assert readiness.ready is False
    assert any("日K" in item for item in readiness.missing)
    assert "kline_daily_xbx" in readiness.missing[0]
    assert r"F:\quant\data\value\stock-trading-data-pro" in readiness.missing[0]
    assert not (tmp_path / "pools").exists()


def test_save_writes_research_snapshot(tmp_path) -> None:
    class ReadyAdapter:
        def readiness(self, month: str) -> DataReadiness:
            return DataReadiness(month, date(2026, 4, 30), True, (), ("市值条件未启用：缺少历史股本",), 200)

        def load(self, readiness: DataReadiness, symbols: set[str]) -> StockPoolInput:
            return _strategy_input()

    watchlist_store = WatchlistPoolStore(tmp_path)
    watchlist_store.create("2026-05")
    watchlist_store.add("month:2026-05", "600000.SH")
    watchlist_store.add("month:2026-05", "000001.SZ")
    service = StockPoolService(SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)))
    service.adapter = ReadyAdapter()
    service.store = StockPoolStore(tmp_path)

    preview = service.build("monthly_growth_trend", "2026-05", source_pool_key="month:2026-05")
    assert preview.status == "ready"
    assert preview.code_string == "000001.SZ,600000.SH"

    saved = service.save("monthly_growth_trend", "2026-05", source_pool_key="month:2026-05")
    run_dir = tmp_path / "pools" / "research" / "monthly_growth_trend" / "pool_month=2026-05" / f"run_id={saved['run_id']}"
    assert (run_dir / "members.parquet").exists()
    assert (run_dir / "manifest.json").exists()
    manifest = service.store.get_run(saved["run_id"])[0]
    assert manifest["data_coverage"]["daily_trading_days_before_as_of"] == 200
    assert manifest["source_pool_key"] == "month:2026-05"
    assert service.list_runs("monthly_growth_trend")[0]["member_count"] == 2
    assert [row["symbol"] for row in WatchlistPoolStore(tmp_path).list_members("month:2026-05")] == ["000001.SZ", "600000.SH"]


def test_screening_uses_full_market_and_creates_generated_pool(tmp_path) -> None:
    class ReadyAdapter:
        def readiness(self, month: str) -> DataReadiness:
            return DataReadiness(month, date(2026, 4, 30), True, (), (), 200)

        def load(self, readiness: DataReadiness, symbols: set[str] | None = None) -> StockPoolInput:
            source = _strategy_input()
            if symbols is None:
                return source
            return StockPoolInput(
                source.month,
                source.as_of_date,
                source.daily.filter(pl.col("symbol").is_in(symbols)),
                source.financials.filter(pl.col("symbol").is_in(symbols)),
                source.instruments.filter(pl.col("symbol").is_in(symbols)),
            )

    watchlist_store = WatchlistPoolStore(tmp_path)
    watchlist_store.create("2026-05")
    watchlist_store.add("month:2026-05", "600000.SH")
    watchlist_store.create("2026-06")
    watchlist_store.add("month:2026-06", "000001.SZ")
    members_path = tmp_path / "user_data" / "watchlist_pools" / "month=2026-05" / "members.parquet"
    manifest_path = tmp_path / "user_data" / "watchlist_pools" / "month=2026-05" / "manifest.json"
    original_members = members_path.read_bytes()
    original_manifest = manifest_path.read_bytes()

    service = StockPoolService(SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)))
    service.adapter = ReadyAdapter()
    service.store = StockPoolStore(tmp_path)

    preview = service.build("monthly_growth_trend", "2026-05")

    assert [member["symbol"] for member in preview.members] == ["000001.SZ", "600000.SH"]
    saved = service.save("monthly_growth_trend", "2026-05")
    assert saved["generated_pool_key"] == "generated:monthly_growth_trend:2026-05"
    assert members_path.read_bytes() == original_members
    assert manifest_path.read_bytes() == original_manifest
    generated_pool = WatchlistPoolStore(tmp_path).get_pool(saved["generated_pool_key"])
    assert generated_pool["source"] == "stock_pool"
    assert generated_pool["month"] == "2026-05"
    assert [row["symbol"] for row in WatchlistPoolStore(tmp_path).list_members(saved["generated_pool_key"])] == ["000001.SZ", "600000.SH"]


def test_monthly_growth_strategy_exposes_editable_parameter_descriptors() -> None:
    spec = get_strategy("monthly_growth_trend")

    assert spec is not None
    assert [item["key"] for item in spec.parameters] == [
        "revenue_yoy_min", "ma_window", "ma_days", "high_window",
        "high_days", "market_cap_min",
    ]
    assert spec.parameters[0]["default"] == 0.15


def test_request_uses_month_without_a_manual_source_template() -> None:
    request = StockPoolBuildRequest(month="2026-05")

    assert request.month == "2026-05"


def test_service_rejects_unsupported_source_template(tmp_path) -> None:
    service = StockPoolService(SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)))

    with pytest.raises(ValueError, match="2026-05 或 2026-06"):
        service.build("monthly_growth_trend", "2026-07", source_pool_key="month:2026-07")
