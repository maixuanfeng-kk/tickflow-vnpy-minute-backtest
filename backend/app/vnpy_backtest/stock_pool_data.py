"""Monthly stock-pool snapshots consumed by the ETF-timed strategy."""
from __future__ import annotations

from pathlib import Path

import polars as pl


def _read_pool_members(data_dir: Path, month: str) -> frozenset[str]:
    path = data_dir / "user_data" / "watchlist_pools" / f"generated=monthly_growth_trend--{month}" / "members.parquet"
    if not path.exists():
        return frozenset()
    try:
        frame = pl.read_parquet(path)
        return frozenset(str(value).strip().upper() for value in frame.get_column("symbol").to_list())
    except (OSError, pl.exceptions.PolarsError, KeyError):
        return frozenset()


def monthly_stock_pools(data_dir: Path) -> dict[str, dict[str, object]]:
    """Return only successfully generated monthly snapshots."""
    result: dict[str, dict[str, object]] = {}
    for month, effective in (
        ("2026-05", "2026-05-06"),
        ("2026-06", "2026-06-01"),
        ("2026-07", "2026-07-01"),
    ):
        members = _read_pool_members(data_dir, month)
        if members:
            result[month] = {"effective_date": effective, "symbols": sorted(members)}
    return result
