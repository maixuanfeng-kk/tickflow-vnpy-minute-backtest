"""Monthly stock-pool snapshots consumed by the ETF-timed strategy."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

_SNAPSHOTS = (
    {"month": "2026-05", "effective_date": "2026-05-06", "as_of_date": "2026-04-30", "member_count": 217},
    {"month": "2026-06", "effective_date": "2026-06-01", "as_of_date": "2026-05-29", "member_count": 260},
    {"month": "2026-07", "effective_date": "2026-07-01", "as_of_date": "2026-06-30", "member_count": 250},
)


def _read_snapshot(data_dir: Path, expected: dict[str, object]) -> dict[str, object]:
    month = str(expected["month"])
    directory = (
        data_dir / "user_data" / "watchlist_pools"
        / f"generated=monthly_growth_trend--{month}"
    )
    manifest_path = directory / "manifest.json"
    members_path = directory / "members.parquet"
    if not manifest_path.exists() or not members_path.exists():
        raise ValueError(f"缺少 {month} 月度股票池快照")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        members = frozenset(
            str(value).strip().upper()
            for value in pl.read_parquet(members_path).get_column("symbol").to_list()
        )
    except (OSError, json.JSONDecodeError, pl.exceptions.PolarsError, KeyError) as exc:
        raise ValueError(f"无法读取 {month} 月度股票池快照") from exc
    required = {
        "pool_key": f"generated:monthly_growth_trend:{month}",
        "month": month,
        "source": "stock_pool",
        "strategy_id": "monthly_growth_trend",
        "as_of_date": expected["as_of_date"],
        "member_count": expected["member_count"],
    }
    mismatched = [key for key, value in required.items() if manifest.get(key) != value]
    if mismatched or len(members) != expected["member_count"]:
        details = ", ".join(mismatched or ["members.parquet"])
        raise ValueError(f"{month} 月度股票池快照校验失败: {details}")
    return {
        "effective_date": expected["effective_date"],
        "as_of_date": expected["as_of_date"],
        "pool_key": required["pool_key"],
        "source": required["source"],
        "member_count": len(members),
        "symbols": sorted(members),
    }


def monthly_stock_pools(data_dir: Path) -> dict[str, dict[str, object]]:
    """Load and validate all snapshots required by the fixed three-month run."""
    return {
        str(expected["month"]): _read_snapshot(data_dir, expected)
        for expected in _SNAPSHOTS
    }


def stock_pool_minute_coverage(
    data_dir: Path,
    pools: dict[str, dict[str, object]],
    required_days: list[str],
) -> dict[str, object]:
    """Report ETF trading days with at least one stock-pool minute bar."""
    symbols = {
        str(symbol)
        for pool in pools.values()
        for symbol in pool.get("symbols", [])
    }
    root = data_dir / "kline_minute_tushare"
    available: list[str] = []
    for day in required_days:
        part = root / f"date={day}" / "part.parquet"
        if not part.exists():
            continue
        try:
            frame = pl.read_parquet(part, columns=["symbol"])
        except (OSError, pl.exceptions.PolarsError):
            continue
        if frame.get_column("symbol").cast(pl.Utf8).is_in(symbols).any():
            available.append(day)
    available_set = set(available)
    return {
        "available_days": available,
        "missing_days": [day for day in required_days if day not in available_set],
        "trading_days": len(available),
    }
