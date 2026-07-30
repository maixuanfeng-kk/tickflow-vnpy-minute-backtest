from __future__ import annotations

from pathlib import Path

import polars as pl

from app.services import watchlist
from app.services.watchlist_pools import WatchlistPoolStore, normalize_symbol


def test_normalize_symbol_converts_exchange_aliases() -> None:
    assert normalize_symbol("600000.XSHG") == "600000.SH"
    assert normalize_symbol("000001.XSHE") == "000001.SZ"
    assert normalize_symbol("830001.XBSE") == "830001.BJ"
    assert normalize_symbol("600000.SH") == "600000.SH"


def test_same_symbol_can_belong_to_multiple_months(tmp_path: Path) -> None:
    store = WatchlistPoolStore(tmp_path)
    store.create("2026-01")
    store.create("2026-02")

    store.add("month:2026-01", "600000.XSHG")
    store.add("month:2026-02", "600000.SH")

    assert [row["symbol"] for row in store.list_members("month:2026-01")] == ["600000.SH"]
    assert [row["symbol"] for row in store.list_members("month:2026-02")] == ["600000.SH"]


def test_readding_member_moves_it_to_top_without_duplication(tmp_path: Path) -> None:
    store = WatchlistPoolStore(tmp_path)
    store.create("2026-01")
    store.add("month:2026-01", "600000.SH", note="first")
    store.add("month:2026-01", "000001.SZ")

    rows = store.add("month:2026-01", "600000.XSHG", note="updated")

    assert [row["symbol"] for row in rows] == ["600000.SH", "000001.SZ"]
    assert rows[0]["note"] == "updated"


def test_replace_updates_current_month_and_keeps_other_month(tmp_path: Path) -> None:
    store = WatchlistPoolStore(tmp_path)
    store.create("2026-01")
    store.create("2026-02")
    store.add("month:2026-01", "600000.SH")
    store.add("month:2026-02", "000001.SZ")

    pool = store.replace(
        "2026-01",
        [{"symbol": "300001.XSHE", "note": "selected"}],
        {"source": "stock_pool_strategy", "source_run_id": "run-1"},
    )

    assert pool["source"] == "stock_pool_strategy"
    assert pool["source_run_id"] == "run-1"
    assert [row["symbol"] for row in store.list_members("month:2026-01")] == ["300001.SZ"]
    assert [row["symbol"] for row in store.list_members("month:2026-02")] == ["000001.SZ"]


def test_aggregate_deduplicates_symbols_and_lists_months(tmp_path: Path) -> None:
    store = WatchlistPoolStore(tmp_path)
    store.create("2026-01")
    store.create("2026-02")
    store.add("month:2026-01", "600000.SH")
    store.add("month:2026-02", "600000.SH")
    store.add("month:2026-02", "000001.SZ")

    rows = {row["symbol"]: row for row in store.aggregate()}

    assert rows["600000.SH"]["pool_keys"] == ["month:2026-02", "month:2026-01"]
    assert rows["000001.SZ"]["pool_keys"] == ["month:2026-02"]


def test_latest_month_is_default_pool(tmp_path: Path) -> None:
    store = WatchlistPoolStore(tmp_path)
    store.create("2026-01")
    store.create("2026-03")
    store.add("month:2026-01", "600000.SH")
    store.add("month:2026-03", "000001.SZ")

    assert store.default_pool_key() == "month:2026-03"
    assert [pool["month"] for pool in store.list_pools() if pool["month"]] == ["2026-03", "2026-01"]


def test_migrate_legacy_watchlist_once_without_changing_source(tmp_path: Path) -> None:
    legacy = tmp_path / "user_data" / "watchlist.parquet"
    legacy.parent.mkdir(parents=True)
    original = pl.DataFrame({
        "symbol": ["600000.XSHG", "000001.XSHE"],
        "added_at": ["2026-07-22T00:00:00", "2026-07-22T00:00:01"],
        "note": ["", "keep"],
    })
    original.write_parquet(legacy)
    source_bytes = legacy.read_bytes()
    store = WatchlistPoolStore(tmp_path)

    assert store.migrate_legacy("2026-01") == 2
    assert store.migrate_legacy("2026-01") == 0
    assert legacy.read_bytes() == source_bytes
    assert store.list_members("month:2026-01") == [
        {"symbol": "600000.SH", "added_at": "2026-07-22T00:00:00", "note": "", "source": "migration"},
        {"symbol": "000001.SZ", "added_at": "2026-07-22T00:00:01", "note": "keep", "source": "migration"},
    ]


def test_legacy_watchlist_service_defaults_to_migrated_latest_month(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / "user_data" / "watchlist.parquet"
    legacy.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600000.XSHG"],
        "added_at": ["2026-07-22T00:00:00"],
        "note": [""],
    }).write_parquet(legacy)
    monkeypatch.setattr(watchlist.settings, "data_dir", tmp_path)

    assert watchlist.list_symbols()[0]["symbol"] == "600000.SH"
    assert (tmp_path / "user_data" / "watchlist_pools" / "month=2026-01").exists()


def test_watchlist_service_scopes_members_by_pool_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(watchlist.settings, "data_dir", tmp_path)
    store = WatchlistPoolStore(tmp_path)
    store.create("2026-01")
    store.create("2026-02")

    watchlist.add("600000.SH", pool_key="month:2026-01")
    watchlist.add("000001.SZ", pool_key="month:2026-02")

    assert [row["symbol"] for row in watchlist.list_symbols("month:2026-01")] == ["600000.SH"]
    assert [row["symbol"] for row in watchlist.list_symbols("month:2026-02")] == ["000001.SZ"]
