"""Persistent, server-validated task catalog for FinSight deep reports."""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Literal

from app.config import settings

TaskKind = Literal["collect", "analysis"]

_LOCK = threading.Lock()
_KINDS: tuple[TaskKind, ...] = ("collect", "analysis")
_TASK_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

_DEFAULT_TASKS: tuple[dict[str, str], ...] = (
    {
        "id": "collect-financial-statements",
        "kind": "collect",
        "title": "三大财务报表",
        "prompt": "资产负债表、利润表、现金流量表三大财务报表",
    },
    {
        "id": "collect-stock-profile-price",
        "kind": "collect",
        "title": "股票基本信息与股价",
        "prompt": "股票基本信息以及股价数据",
    },
    {
        "id": "collect-shareholder-structure",
        "kind": "collect",
        "title": "股东结构",
        "prompt": "股东结构",
    },
    {
        "id": "collect-investment-ratings",
        "kind": "collect",
        "title": "投资评级",
        "prompt": "投资评级",
    },
    {
        "id": "collect-valuation-metrics",
        "kind": "collect",
        "title": "估值与盈利指标",
        "prompt": "公司市销率、净资产收益率(ROE)、市盈率、市净率",
    },
    {
        "id": "collect-competitors",
        "kind": "collect",
        "title": "主要竞争对手",
        "prompt": "公司主要竞争对手情况",
    },
    {
        "id": "collect-market-indices",
        "kind": "collect",
        "title": "市场指数数据",
        "prompt": "指数数据: 沪深300指数日数据、恒生指数日数据、上证指数日数据、纳斯达克指数日数据",
    },
    {
        "id": "analysis-company-history",
        "kind": "analysis",
        "title": "公司历程与主营业务",
        "prompt": "梳理公司发展历程、关键里程碑事件及当前核心主营业务范围",
    },
    {
        "id": "analysis-management-shareholders",
        "kind": "analysis",
        "title": "管理层与股权结构",
        "prompt": "分析创始团队及高管背景，梳理股权结构及主要股东情况",
    },
    {
        "id": "analysis-pre-ipo-financing",
        "kind": "analysis",
        "title": "上市前融资",
        "prompt": "梳理公司上市前的融资轮次、金额及主要投资方",
    },
    {
        "id": "analysis-revenue-growth",
        "kind": "analysis",
        "title": "营收与业务增长",
        "prompt": "分析历年营收趋势、各业务板块占比变化及增长驱动因素",
    },
    {
        "id": "analysis-profitability",
        "kind": "analysis",
        "title": "盈利能力与运营效率",
        "prompt": "评估公司盈利能力(ROE、毛利率、净利率)及运营效率(各项周转率)",
    },
    {
        "id": "analysis-solvency-cashflow",
        "kind": "analysis",
        "title": "偿债能力与现金流",
        "prompt": "分析公司偿债能力(资产负债率、流动比率)及现金流结构与健康度",
    },
    {
        "id": "analysis-competition",
        "kind": "analysis",
        "title": "行业竞争力",
        "prompt": "进行同行业竞争对手对比分析，评估行业地位及核心竞争力(技术、品牌、渠道)",
    },
    {
        "id": "analysis-stock-performance",
        "kind": "analysis",
        "title": "股价表现与事件影响",
        "prompt": "复盘近三年股价走势与成交量，分析关键事件(政策、财报、技术)对股价的影响",
    },
    {
        "id": "analysis-financial-forecast",
        "kind": "analysis",
        "title": "财务预测与估值",
        "prompt": "整理历史三大财务报表，预测未来两年核心财务数据，并进行估值分析",
    },
)


def _catalog_path() -> Path:
    return settings.data_dir / "user_data" / "deep_report_task_catalog.json"


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_kind(value: str) -> TaskKind:
    kind = str(value or "").strip()
    if kind not in _KINDS:
        raise ValueError("Task kind must be collect or analysis.")
    return kind  # type: ignore[return-value]


def _validate_task_id(value: str) -> str:
    task_id = str(value or "").strip()
    if not _TASK_ID_RE.fullmatch(task_id):
        raise ValueError("Task ID is invalid.")
    return task_id


def _validate_text(value: str, *, field: str, maximum: int, multiline: bool) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Task {field} is required.")
    if len(text) > maximum:
        raise ValueError(f"Task {field} must not exceed {maximum} characters.")
    for character in text:
        if ord(character) < 32 and not (multiline and character in {"\n", "\r", "\t"}):
            raise ValueError(f"Task {field} contains control characters.")
    return text


def _seed_catalog() -> dict[str, Any]:
    tasks = [{**task, "enabled": True, "order": order} for order, task in enumerate(_DEFAULT_TASKS, 1)]
    return {"version": 1, "tasks": tasks}


def _normalize_task(task: dict[str, Any], *, order: int) -> dict[str, Any]:
    if not isinstance(task, dict):
        raise ValueError("Each task must be an object.")
    normalized_order = int(task.get("order", order))
    if normalized_order <= 0:
        raise ValueError("Task order must be positive.")
    return {
        "id": _validate_task_id(str(task.get("id") or uuid.uuid4().hex)),
        "kind": _validate_kind(str(task.get("kind", ""))),
        "title": _validate_text(task.get("title", ""), field="title", maximum=120, multiline=False),
        "prompt": _validate_text(task.get("prompt", ""), field="prompt", maximum=8000, multiline=True),
        "enabled": bool(task.get("enabled", True)),
        "order": normalized_order,
    }


def _load_catalog() -> dict[str, Any]:
    path = _catalog_path()
    if not path.exists():
        catalog = _seed_catalog()
        _save_catalog(catalog)
        return catalog
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("tasks"), list):
            raise ValueError("tasks must be a list")
        tasks = [_normalize_task(task, order=index) for index, task in enumerate(raw["tasks"], 1)]
        task_ids = [task["id"] for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task IDs must be unique")
        return {"version": int(raw.get("version", 1)), "tasks": tasks}
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Deep report task catalog is invalid: {exc}") from exc


def _save_catalog(catalog: dict[str, Any]) -> None:
    _atomic_write_json(_catalog_path(), catalog)


def _sorted(tasks: list[dict[str, Any]], kind: TaskKind, *, active_only: bool) -> list[dict[str, Any]]:
    result = [task for task in tasks if task["kind"] == kind and (task["enabled"] or not active_only)]
    return sorted(result, key=lambda task: (task["order"], task["title"], task["id"]))


def get_catalog(*, active_only: bool = False) -> dict[str, list[dict[str, Any]]]:
    with _LOCK:
        tasks = _load_catalog()["tasks"]
    return {
        "collect_tasks": _sorted(tasks, "collect", active_only=active_only),
        "analysis_tasks": _sorted(tasks, "analysis", active_only=active_only),
    }


def create_task(*, kind: TaskKind, title: str, prompt: str, enabled: bool = True) -> dict[str, Any]:
    validated_kind = _validate_kind(kind)
    with _LOCK:
        catalog = _load_catalog()
        tasks = catalog["tasks"]
        order = max((int(task["order"]) for task in tasks if task["kind"] == validated_kind), default=0) + 1
        task = _normalize_task(
            {"kind": validated_kind, "title": title, "prompt": prompt, "enabled": enabled, "order": order},
            order=order,
        )
        tasks.append(task)
        _save_catalog(catalog)
    return task


def update_task(task_id: str, *, kind: TaskKind, title: str, prompt: str, enabled: bool) -> dict[str, Any]:
    validated_id = _validate_task_id(task_id)
    validated_kind = _validate_kind(kind)
    with _LOCK:
        catalog = _load_catalog()
        for index, existing in enumerate(catalog["tasks"]):
            if existing["id"] != validated_id:
                continue
            order = existing["order"]
            if existing["kind"] != validated_kind:
                order = max(
                    (int(task["order"]) for task in catalog["tasks"] if task["kind"] == validated_kind),
                    default=0,
                ) + 1
            updated = _normalize_task(
                {
                    "id": validated_id,
                    "kind": validated_kind,
                    "title": title,
                    "prompt": prompt,
                    "enabled": enabled,
                    "order": order,
                },
                order=order,
            )
            catalog["tasks"][index] = updated
            _save_catalog(catalog)
            return updated
    raise FileNotFoundError(f"Deep report task not found: {validated_id}")


def delete_task(task_id: str) -> None:
    validated_id = _validate_task_id(task_id)
    with _LOCK:
        catalog = _load_catalog()
        tasks = [task for task in catalog["tasks"] if task["id"] != validated_id]
        if len(tasks) == len(catalog["tasks"]):
            raise FileNotFoundError(f"Deep report task not found: {validated_id}")
        catalog["tasks"] = tasks
        _save_catalog(catalog)


def reorder_tasks(kind: TaskKind, task_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    validated_kind = _validate_kind(kind)
    validated_ids = [_validate_task_id(task_id) for task_id in task_ids]
    if len(validated_ids) != len(set(validated_ids)):
        raise ValueError("Task order must not contain duplicate IDs.")
    with _LOCK:
        catalog = _load_catalog()
        target = _sorted(catalog["tasks"], validated_kind, active_only=False)
        known_ids = {task["id"] for task in target}
        if set(validated_ids) != known_ids:
            raise ValueError("Task order must contain every task ID in the selected type exactly once.")
        order_by_id = {task_id: index for index, task_id in enumerate(validated_ids, 1)}
        for task in catalog["tasks"]:
            if task["kind"] == validated_kind:
                task["order"] = order_by_id[task["id"]]
        _save_catalog(catalog)
        tasks = catalog["tasks"]
    return {
        "collect_tasks": _sorted(tasks, "collect", active_only=False),
        "analysis_tasks": _sorted(tasks, "analysis", active_only=False),
    }


def resolve_task_prompts(*, collect_task_ids: list[str], analysis_task_ids: list[str]) -> tuple[list[str], list[str]]:
    with _LOCK:
        catalog = _load_catalog()
    by_id = {task["id"]: task for task in catalog["tasks"]}

    def resolve(task_ids: list[str], kind: TaskKind) -> list[str]:
        if not task_ids:
            raise ValueError(f"At least one {kind} task is required.")
        normalized_ids = [_validate_task_id(task_id) for task_id in task_ids]
        if len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError(f"Duplicate {kind} task IDs are not allowed.")
        prompts: list[str] = []
        for task_id in normalized_ids:
            task = by_id.get(task_id)
            if task is None or task["kind"] != kind or not task["enabled"]:
                raise ValueError(f"Unknown or disabled {kind} task: {task_id}")
            prompts.append(task["prompt"])
        return prompts

    return resolve(collect_task_ids, "collect"), resolve(analysis_task_ids, "analysis")
