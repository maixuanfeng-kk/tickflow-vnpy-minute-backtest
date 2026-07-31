"""Persistent operational watchlist pools grouped by month."""
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
    aliases = {".XSHG": ".SH", ".XSHE": ".SZ", ".XBSE": ".BJ"}
    for source, target in aliases.items():
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
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "user_data" / "watchlist_pools"

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

    def create(self, month: str | None) -> dict:
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

    def list_pools(self) -> list[dict]:
        if not self.root.exists():
            return []
        pools: list[dict] = []
        for path in self.root.glob("*/manifest.json"):
            try:
                pools.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(
            pools,
            key=lambda item: (item.get("month") is not None, str(item.get("month") or "")),
            reverse=True,
        )

    def get_pool(self, pool_key: str) -> dict | None:
        path = self._manifest_path(pool_key)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def default_pool_key(self) -> str:
        monthly = [pool for pool in self.list_pools() if pool.get("month")]
        if monthly:
            return str(monthly[0]["pool_key"])
        return "ungrouped"

    def list_members(self, pool_key: str | None = None) -> list[dict]:
        resolved = pool_key or self.default_pool_key()
        path = self._members_path(resolved)
        if not path.exists():
            return []
        frame = pl.read_parquet(path)
        return frame.to_dicts() if not frame.is_empty() else []

    def add(
        self,
        pool_key: str,
        symbol: str,
        note: str = "",
        source: str = "manual",
    ) -> list[dict]:
        if self.get_pool(pool_key) is None:
            if pool_key == "ungrouped":
                self.create(None)
            else:
                raise KeyError(pool_key)
        normalized = normalize_symbol(symbol)
        existing = [row for row in self.list_members(pool_key) if row["symbol"] != normalized]
        rows = [{
            "symbol": normalized,
            "added_at": self._now(),
            "note": note,
            "source": source,
        }, *existing]
        self._update_members(pool_key, rows)
        return rows

    def remove(self, pool_key: str, symbol: str) -> list[dict]:
        normalized = normalize_symbol(symbol)
        rows = [row for row in self.list_members(pool_key) if row["symbol"] != normalized]
        self._update_members(pool_key, rows)
        return rows

    def move_to_top(self, pool_key: str, symbol: str) -> list[dict]:
        normalized = normalize_symbol(symbol)
        rows = self.list_members(pool_key)
        target = [row for row in rows if row["symbol"] == normalized]
        if not target:
            return rows
        ordered = [*target, *[row for row in rows if row["symbol"] != normalized]]
        self._update_members(pool_key, ordered)
        return ordered

    def clear(self, pool_key: str) -> int:
        count = len(self.list_members(pool_key))
        self._update_members(pool_key, [])
        return count

    def replace(self, month: str, members: list[dict], metadata: dict) -> dict:
        pool_key = pool_key_for_month(month)
        old = self.get_pool(pool_key)
        now = self._now()
        seen: set[str] = set()
        rows: list[dict] = []
        for member in members:
            symbol = normalize_symbol(str(member.get("symbol", "")))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            rows.append({
                "symbol": symbol,
                "added_at": str(member.get("added_at") or now),
                "note": str(member.get("note") or ""),
                "source": str(member.get("source") or metadata.get("source") or "manual"),
            })
        manifest = {
            "pool_key": pool_key,
            "month": month,
            "created_at": (old or {}).get("created_at", now),
            "updated_at": now,
            **metadata,
            "member_count": len(rows),
        }
        self._write_pool(pool_key, manifest, rows)
        return manifest

    def replace_generated(self, strategy_id: str, month: str, members: list[dict], metadata: dict) -> dict:
        pool_key = generated_pool_key(strategy_id, month)
        old = self.get_pool(pool_key)
        now = self._now()
        seen: set[str] = set()
        rows: list[dict] = []
        for member in members:
            symbol = normalize_symbol(str(member.get("symbol", "")))
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            rows.append({
                "symbol": symbol,
                "added_at": now,
                "note": str(member.get("note") or ""),
                "source": "stock_pool",
            })
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

    def aggregate(self) -> list[dict]:
        combined: dict[str, dict] = {}
        for pool in self.list_pools():
            pool_key = str(pool["pool_key"])
            for row in self.list_members(pool_key):
                symbol = str(row["symbol"])
                if symbol not in combined:
                    combined[symbol] = {**row, "pool_keys": []}
                combined[symbol]["pool_keys"].append(pool_key)
        return list(combined.values())

    def migrate_legacy(self, month: str = "2026-01") -> int:
        pool_key = pool_key_for_month(month)
        if self.get_pool(pool_key) is not None:
            return 0
        legacy = self.data_dir / "user_data" / "watchlist.parquet"
        if not legacy.exists():
            return 0
        frame = pl.read_parquet(legacy)
        rows = [
            {
                "symbol": row.get("symbol", ""),
                "added_at": row.get("added_at"),
                "note": row.get("note", ""),
                "source": "migration",
            }
            for row in frame.to_dicts()
        ]
        self.replace(month, rows, {"source": "migration"})
        return len(rows)

    def _update_members(self, pool_key: str, rows: list[dict]) -> None:
        manifest = self.get_pool(pool_key)
        if manifest is None:
            raise KeyError(pool_key)
        manifest = {**manifest, "updated_at": self._now(), "member_count": len(rows)}
        self._write_pool(pool_key, manifest, rows)

    def _write_pool(self, pool_key: str, manifest: dict, rows: list[dict]) -> None:
        directory = self._directory(pool_key)
        directory.mkdir(parents=True, exist_ok=True)
        members_path = directory / "members.parquet"
        manifest_path = directory / "manifest.json"
        members_tmp = directory / "members.parquet.tmp"
        manifest_tmp = directory / "manifest.json.tmp"
        pl.DataFrame(rows, schema=_MEMBER_SCHEMA).write_parquet(members_tmp)
        manifest_tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        members_tmp.replace(members_path)
        manifest_tmp.replace(manifest_path)
