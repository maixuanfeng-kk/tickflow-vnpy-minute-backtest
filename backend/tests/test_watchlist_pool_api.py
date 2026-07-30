from __future__ import annotations

from types import SimpleNamespace

import polars as pl

from app.api import watchlist as watchlist_api
from app.services import watchlist


def _request() -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=SimpleNamespace(
        get_name_map=lambda symbols: {},
    ))))


def test_pool_routes_create_and_scope_members(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(watchlist.settings, "data_dir", tmp_path)
    request = _request()

    created = watchlist_api.create_pool(watchlist_api.CreatePoolRequest(month="2026-02"))
    watchlist_api.add_one(
        watchlist_api.AddRequest(symbol="600000.XSHG"),
        request,
        pool_key="month:2026-02",
    )

    assert created["pool"]["pool_key"] == "month:2026-02"
    assert watchlist_api.list_all(request, pool_key="month:2026-02")["symbols"][0]["symbol"] == "600000.SH"
    assert watchlist_api.list_all(request, view="all")["symbols"][0]["pool_keys"] == ["month:2026-02"]


def test_legacy_migration_status_and_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(watchlist.settings, "data_dir", tmp_path)
    legacy = tmp_path / "user_data" / "watchlist.parquet"
    legacy.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600000.XSHG", "000001.XSHE"],
        "added_at": ["2026-07-22T00:00:00", "2026-07-22T00:00:01"],
        "note": ["", ""],
    }).write_parquet(legacy)

    before = watchlist_api.legacy_migration_status()
    result = watchlist_api.migrate_legacy_watchlist()
    after = watchlist_api.legacy_migration_status()

    assert before == {"legacy_count": 2, "target_month": "2026-01", "target_count": 0, "migrated": False}
    assert result["migrated_count"] == 2
    assert result["pool"]["pool_key"] == "month:2026-01"
    assert after == {"legacy_count": 2, "target_month": "2026-01", "target_count": 2, "migrated": True}
