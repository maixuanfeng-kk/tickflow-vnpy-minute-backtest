"""Persistent monthly watchlist pools, isolated from the legacy watchlist file."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import polars as pl


_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MEMBER_SCHEMA = {
    "symbol": pl.Utf8,
    "added_at": pl.Utf8,
    "note": pl.Utf8,
    "source": pl.Utf8,
}


def normalize_symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    for source, target in {".XSHG": ".SH", ".XSHE": ".SZ", ".XBSE": ".BJ"}.items():
        if value.endswith(source):
            return value[: -len(source)] + target
    return value


def pool_key_for_month(month: str) -> str:
    if not _MONTH_RE.fullmatch(month):
        raise ValueError("月份格式必须为 YYYY-MM")
    return f"month:{month}"


def generated_pool_key(strategy_id: str, month: str) -> str:
    pool_key_for_month(month)
    if not re.fullmatch(r"[a-z0-9_]+", strategy_id):
        raise ValueError("无效的生成股票池策略标识")
    return f"generated:{strategy_id}:{month}"


class WatchlistPoolStore:
    """Store named pools under user_data without changing watchlist.parquet."""

    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "user_data" / "watchlist_pools"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _directory_name(pool_key: str) -> str:
        if pool_key == "ungrouped":
            return "ungrouped"
        if pool_key.startswith("month:"):
            month = pool_key.split(":", 1)[1]
            pool_key_for_month(month)
            return f"month={month}"
        if pool_key.startswith("generated:"):
            _, strategy_id, month = pool_key.split(":", 2)
            generated_pool_key(strategy_id, month)
            return f"generated={strategy_id}--{month}"
        raise ValueError("无效的股票池标识")

    def _directory(self, pool_key: str) -> Path:
        return self.root / self._directory_name(pool_key)

    def _manifest_path(self, pool_key: str) -> Path:
        return self._directory(pool_key) / "manifest.json"

    def _members_path(self, pool_key: str) -> Path:
        return self._directory(pool_key) / "members.parquet"

    def get_pool(self, pool_key: str) -> dict[str, object] | None:
        try:
            return json.loads(self._manifest_path(pool_key).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def list_pools(self) -> list[dict[str, object]]:
        if not self.root.exists():
            return []
        result: list[dict[str, object]] = []
        for manifest in self.root.glob("*/manifest.json"):
            try:
                result.append(json.loads(manifest.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(result, key=lambda item: str(item.get("updated_at", item.get("saved_at", ""))), reverse=True)

    def create(self, month: str | None) -> dict[str, object]:
        pool_key = "ungrouped" if month is None else pool_key_for_month(month)
        existing = self.get_pool(pool_key)
        if existing is not None:
            return existing
        now = self._now()
        manifest = {
            "pool_key": pool_key,
            "month": month,
            "source": "manual",
            "created_at": now,
            "updated_at": now,
            "member_count": 0,
        }
        self._write_pool(pool_key, manifest, [])
        return manifest

    def default_pool_key(self) -> str:
        monthly = [item for item in self.list_pools() if item.get("month")]
        return str(monthly[0]["pool_key"]) if monthly else "ungrouped"

    def list_members(self, pool_key: str | None = None) -> list[dict[str, object]]:
        resolved = pool_key or self.default_pool_key()
        path = self._members_path(resolved)
        if not path.exists():
            return []
        frame = pl.read_parquet(path)
        return frame.to_dicts() if not frame.is_empty() else []

    def aggregate(self) -> list[dict[str, object]]:
        combined: dict[str, dict[str, object]] = {}
        for pool in self.list_pools():
            pool_key = str(pool["pool_key"])
            for row in self.list_members(pool_key):
                symbol = str(row["symbol"])
                if symbol not in combined:
                    combined[symbol] = {**row, "pool_keys": []}
                combined[symbol]["pool_keys"].append(pool_key)
        return list(combined.values())

    def add(self, pool_key: str, symbol: str, note: str = "", source: str = "manual") -> list[dict[str, object]]:
        if self.get_pool(pool_key) is None:
            raise KeyError(pool_key)
        normalized = normalize_symbol(symbol)
        rows = [row for row in self.list_members(pool_key) if row.get("symbol") != normalized]
        rows.insert(0, {"symbol": normalized, "added_at": self._now(), "note": note, "source": source})
        self._update_members(pool_key, rows)
        return rows

    def remove(self, pool_key: str, symbol: str) -> list[dict[str, object]]:
        rows = [row for row in self.list_members(pool_key) if row.get("symbol") != normalize_symbol(symbol)]
        self._update_members(pool_key, rows)
        return rows

    def move_to_top(self, pool_key: str, symbol: str) -> list[dict[str, object]]:
        normalized = normalize_symbol(symbol)
        rows = self.list_members(pool_key)
        target = [row for row in rows if row.get("symbol") == normalized]
        if target:
            rows = target + [row for row in rows if row.get("symbol") != normalized]
            self._update_members(pool_key, rows)
        return rows

    def clear(self, pool_key: str) -> int:
        count = len(self.list_members(pool_key))
        self._update_members(pool_key, [])
        return count

    def replace_generated(self, strategy_id: str, month: str, members: list[dict], metadata: dict[str, object]) -> dict[str, object]:
        pool_key = generated_pool_key(strategy_id, month)
        old = self.get_pool(pool_key)
        now = self._now()
        seen: set[str] = set()
        rows: list[dict[str, object]] = []
        for member in members:
            symbol = normalize_symbol(str(member.get("symbol", "")))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            rows.append({"symbol": symbol, "added_at": now, "note": "", "source": "stock_pool"})
        manifest = {
            "pool_key": pool_key,
            "month": month,
            "label": f"{month} 条件选股结果",
            "source": "stock_pool",
            "strategy_id": strategy_id,
            "created_at": (old or {}).get("created_at", now),
            "updated_at": now,
            **metadata,
            "member_count": len(rows),
        }
        self._write_pool(pool_key, manifest, rows)
        return manifest

    def migrate_legacy(self, month: str = "2026-01") -> int:
        pool_key = pool_key_for_month(month)
        if self.get_pool(pool_key) is not None:
            return 0
        legacy = self.root.parent / "watchlist.parquet"
        if not legacy.exists():
            return 0
        rows = [
            {"symbol": normalize_symbol(str(row.get("symbol", ""))), "added_at": str(row.get("added_at") or self._now()),
             "note": str(row.get("note") or ""), "source": "migration"}
            for row in pl.read_parquet(legacy).to_dicts()
            if row.get("symbol")
        ]
        manifest = self.create(month)
        manifest.update({"source": "migration", "member_count": len(rows), "updated_at": self._now()})
        self._write_pool(pool_key, manifest, rows)
        return len(rows)

    def _update_members(self, pool_key: str, rows: list[dict[str, object]]) -> None:
        manifest = self.get_pool(pool_key)
        if manifest is None:
            raise KeyError(pool_key)
        self._write_pool(pool_key, {**manifest, "updated_at": self._now(), "member_count": len(rows)}, rows)

    def _write_pool(self, pool_key: str, manifest: dict[str, object], rows: list[dict[str, object]]) -> None:
        directory = self._directory(pool_key)
        directory.mkdir(parents=True, exist_ok=True)
        members_tmp = directory / "members.parquet.tmp"
        manifest_tmp = directory / "manifest.json.tmp"
        pl.DataFrame(rows, schema=_MEMBER_SCHEMA).write_parquet(members_tmp)
        manifest_tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        members_tmp.replace(directory / "members.parquet")
        manifest_tmp.replace(directory / "manifest.json")
