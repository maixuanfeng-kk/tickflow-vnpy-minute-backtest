from pathlib import Path

from app.services.watchlist_pools import WatchlistPoolStore, generated_pool_key


def test_monthly_pool_storage_is_isolated_and_normalizes_symbols(tmp_path: Path) -> None:
    store = WatchlistPoolStore(tmp_path)
    created = store.create("2026-05")
    assert created["pool_key"] == "month:2026-05"

    store.add("month:2026-05", "600000.XSHG", "manual")
    members = store.list_members("month:2026-05")
    assert [row["symbol"] for row in members] == ["600000.SH"]
    assert not (tmp_path / "user_data" / "watchlist.parquet").exists()


def test_generated_pool_replaces_only_its_own_snapshot(tmp_path: Path) -> None:
    store = WatchlistPoolStore(tmp_path)
    store.create("2026-05")
    store.add("month:2026-05", "000001.SZ")

    generated = store.replace_generated(
        "monthly_growth_trend",
        "2026-05",
        [{"symbol": "600000.SH"}, {"symbol": "600000.SH"}],
        {"run_id": "run-1"},
    )

    assert generated["pool_key"] == generated_pool_key("monthly_growth_trend", "2026-05")
    assert [row["symbol"] for row in store.list_members("month:2026-05")] == ["000001.SZ"]
    assert [row["symbol"] for row in store.list_members(generated["pool_key"])] == ["600000.SH"]
