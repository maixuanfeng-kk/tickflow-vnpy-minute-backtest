"""Application service for previewing and saving monthly stock pools."""
from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from app.stock_pools.data import StockPoolDataAdapter
from app.stock_pools.registry import get_strategy
from app.stock_pools.store import StockPoolStore


@dataclass
class StockPoolBuildResult:
    status: str
    strategy_id: str
    month: str
    readiness: dict[str, object]
    warnings: list[str]
    members: list[dict]
    code_string: str
    summary: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            **self.__dict__,
            "as_of_date": self.readiness.get("as_of_date"),
        }


class StockPoolService:
    def __init__(self, repo) -> None:
        self.adapter = StockPoolDataAdapter(repo.store.data_dir)
        self.store = StockPoolStore(repo.store.data_dir)

    def readiness(self, month: str) -> dict[str, object]:
        return self.adapter.readiness(month).to_dict()

    def build(self, strategy_id: str, month: str, params: dict[str, object] | None = None) -> StockPoolBuildResult:
        strategy_spec = get_strategy(strategy_id)
        if strategy_spec is None:
            raise ValueError(f"不支持的股票池策略: {strategy_id}")
        readiness = self.adapter.readiness(month)
        if not readiness.ready:
            return StockPoolBuildResult("not_ready", strategy_id, month, readiness.to_dict(), list(readiness.warnings), [], "", {})
        members = strategy_spec.strategy_class(params or {}).build(self.adapter.load(readiness))
        records = self._records(members)
        return StockPoolBuildResult(
            "ready", strategy_id, month, readiness.to_dict(), list(readiness.warnings), records,
            ",".join(row["symbol"] for row in records),
            {"selected_count": len(records), "condition_1_count": sum(bool(row["condition_1"]) for row in records),
             "condition_2_count": sum(bool(row["condition_2"]) for row in records)},
        )

    def save(self, strategy_id: str, month: str, params: dict[str, object] | None = None) -> dict[str, object]:
        result = self.build(strategy_id, month, params)
        if result.status != "ready":
            raise RuntimeError("数据尚未就绪，不能保存股票池")
        members = pl.DataFrame(result.members)
        manifest = {
            "as_of_date": result.readiness.get("as_of_date"),
            "readiness": result.readiness,
            "data_coverage": result.readiness.get("coverage", {}),
            "warnings": result.warnings,
            "summary": result.summary,
            "strategy_version": get_strategy(strategy_id).version,
            "params": params or {},
        }
        saved = self.store.save(strategy_id=strategy_id, month=month, members=members, manifest=manifest)
        return {
            **result.to_dict(),
            "run_id": saved["run_id"],
            "saved_at": saved["saved_at"],
        }

    def list_runs(self, strategy_id: str | None = None) -> list[dict[str, object]]:
        runs = self.store.list_runs()
        if strategy_id:
            runs = [item for item in runs if item.get("strategy_id") == strategy_id]
        return [
            {
                "run_id": item.get("run_id"),
                "strategy_id": item.get("strategy_id"),
                "month": item.get("month"),
                "as_of_date": item.get("as_of_date"),
                "member_count": item.get("summary", {}).get("selected_count", 0),
                "saved_at": item.get("saved_at"),
                "warnings": item.get("warnings", []),
            }
            for item in runs
        ]

    def get_run(self, run_id: str) -> dict[str, object] | None:
        loaded = self.store.get_run(run_id)
        if loaded is None:
            return None
        manifest, members = loaded
        records = self._records(members)
        return {
            "status": "ready",
            "run_id": manifest.get("run_id"),
            "saved_at": manifest.get("saved_at"),
            "strategy_id": manifest.get("strategy_id"),
            "month": manifest.get("month"),
            "as_of_date": manifest.get("as_of_date"),
            "readiness": manifest.get("readiness", {}),
            "warnings": manifest.get("warnings", []),
            "summary": manifest.get("summary", {}),
            "members": records,
            "code_string": ",".join(row["symbol"] for row in records),
        }

    @staticmethod
    def _records(frame: pl.DataFrame) -> list[dict]:
        records: list[dict] = []
        for row in frame.to_dicts():
            records.append({key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in row.items()})
        return records
