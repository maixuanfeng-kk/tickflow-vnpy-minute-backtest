"""Persistence for user-saved stock-pool snapshots."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl


class StockPoolStore:
    def __init__(self, data_dir: Path) -> None:
        self.root = Path(data_dir) / "pools" / "research"

    def save(self, *, strategy_id: str, month: str, members: pl.DataFrame, manifest: dict[str, object]) -> dict[str, object]:
        run_id = uuid4().hex[:12]
        directory = self.root / strategy_id / f"pool_month={month}" / f"run_id={run_id}"
        directory.mkdir(parents=True, exist_ok=True)
        members.write_parquet(directory / "members.parquet")
        payload = {**manifest, "run_id": run_id, "strategy_id": strategy_id, "month": month,
                   "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
        (directory / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return payload

    def list_runs(self) -> list[dict[str, object]]:
        if not self.root.exists():
            return []
        records: list[dict[str, object]] = []
        for path in self.root.glob("*/pool_month=*/run_id=*/manifest.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                records.append(record)
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(records, key=lambda item: str(item.get("saved_at", "")), reverse=True)

    def get_run(self, run_id: str) -> tuple[dict[str, object], pl.DataFrame] | None:
        for manifest in self.root.glob(f"*/pool_month=*/run_id={run_id}/manifest.json"):
            try:
                return json.loads(manifest.read_text(encoding="utf-8")), pl.read_parquet(manifest.parent / "members.parquet")
            except (OSError, json.JSONDecodeError):
                return None
        return None
