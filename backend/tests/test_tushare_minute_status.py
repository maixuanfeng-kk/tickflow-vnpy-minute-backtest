from types import SimpleNamespace

from app.api.data import _safe_aggregate_minute


def test_minute_status_uses_tushare_partitions_when_standard_store_is_empty(tmp_path) -> None:
    (tmp_path / "kline_minute_tushare" / "date=2026-01-05").mkdir(parents=True)
    (tmp_path / "kline_minute_tushare" / "date=2026-01-05" / "part.parquet").touch()
    (tmp_path / "kline_minute_tushare" / "date=2026-01-06").mkdir(parents=True)
    (tmp_path / "kline_minute_tushare" / "date=2026-01-06" / "part.parquet").touch()

    repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))

    assert _safe_aggregate_minute(repo) == {
        "rows": 0,
        "earliest_date": "2026-01-05",
        "latest_date": "2026-01-06",
        "symbols_covered": 0,
        "trading_days": 2,
    }
