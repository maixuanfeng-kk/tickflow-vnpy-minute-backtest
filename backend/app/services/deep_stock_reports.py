"""FinSight deep-report orchestration and persisted run metadata."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from app.config import settings
from app.services import deep_report_tasks

logger = logging.getLogger(__name__)

_INDEX_LOCK = threading.Lock()
_RUN_ID_RE = re.compile(r"^dsr_[0-9]{13}_[a-z0-9-]{1,16}_[a-f0-9]{8}$")
_A_SHARE_RE = re.compile(r"^(?P<code>[0-9]{6})(?:\.(?P<exchange>SH|SZ|BJ))?$")
_HK_RE = re.compile(r"^(?P<code>[0-9]{1,5})\.HK$")
_US_RE = re.compile(r"^(?P<code>[A-Z][A-Z0-9.-]{0,9})\.(?P<exchange>US|NASDAQ|NYSE)$")
_ACTIVE_STATUSES = {"queued", "running"}
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_FINSIGHT_ENV_GROUPS = (
    ("DS_MODEL_NAME",),
    ("DS_BASE_URL",),
    ("DS_API_KEY",),
    ("VLM_MODEL_NAME",),
    ("VLM_BASE_URL",),
    ("VLM_API_KEY",),
    ("EMBEDDING_MODEL_NAME",),
    ("EMBEDDING_BASE_URL",),
    ("EMBEDDING_API_KEY",),
    ("SERPER_API_KEY", "BOCHAAI_API_KEY"),
)

_ARTIFACTS = {
    "markdown": ("report.md", "text/markdown; charset=utf-8", "Markdown"),
    "word": (
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Word",
    ),
    "pdf": ("report.pdf", "application/pdf", "PDF"),
    "log": ("run.log", "text/plain; charset=utf-8", "Log"),
}


class IntegrationUnavailableError(RuntimeError):
    """FinSight is not configured or its runtime is incomplete."""


class ConcurrentRunLimitError(RuntimeError):
    """The configured number of active report runs has been reached."""


class ActiveRunError(RuntimeError):
    """An operation cannot be performed while a report is active."""


class RunnerStartError(RuntimeError):
    """The report request was persisted but its worker could not start."""


class UnsafeRunPathError(RuntimeError):
    """A persisted run directory was redirected outside its trusted root."""


def default_task_templates() -> dict[str, list[str]]:
    catalog = deep_report_tasks.get_catalog(active_only=True)
    return {
        "collect_tasks": [task["prompt"] for task in catalog["collect_tasks"]],
        "analysis_tasks": [task["prompt"] for task in catalog["analysis_tasks"]],
    }


def integration_health() -> dict[str, Any]:
    root = settings.finsight_root
    python_path = _finsight_python()
    base_config = root / "my_config.yaml" if root is not None else None
    env_path = root / ".env" if root is not None else None
    source_root = root / "src" if root is not None else None
    report_template = source_root / "template" / "report_template.docx" if source_root else None
    runner = _runner_script()

    pandoc_available = bool(shutil.which("pandoc"))
    if root is not None:
        candidates = (
            root / ".venv" / "Library" / "bin" / "pandoc.exe",
            root / ".venv" / "bin" / "pandoc",
        )
        pandoc_available = pandoc_available or any(path.is_file() for path in candidates)

    root_trusted = _unredirected_directory(root)
    env_file_exists = _unredirected_file(env_path, root)
    env_ready = env_file_exists and _finsight_env_ready(env_path)
    checks = {
        "windows_supported": os.name == "nt",
        "finsight_root_exists": root_trusted,
        "finsight_python_exists": _unredirected_executable(python_path),
        "base_config_exists": _unredirected_file(base_config, root),
        "finsight_env_exists": env_file_exists,
        "finsight_env_ready": env_ready,
        "finsight_source_exists": _unredirected_directory(source_root),
        "reference_template_exists": _unredirected_file(
            report_template,
            source_root / "template" if source_root else None,
        ),
        "runner_script_exists": runner.is_file(),
        "pandoc_available": pandoc_available,
        "word_detected": _word_detected(),
    }
    ready = all(checks.values())

    warnings: list[str] = []
    if root is None:
        warnings.append("FinSight is not configured. Set FINSIGHT_ROOT.")
    elif not checks["finsight_root_exists"]:
        warnings.append("The configured FinSight root is unavailable or redirected.")
    if not checks["windows_supported"]:
        warnings.append("Full deep-report rendering is supported only on Windows.")
    if not checks["finsight_python_exists"]:
        warnings.append("FinSight Python is unavailable. Set FINSIGHT_PYTHON or run the setup script.")
    if not checks["base_config_exists"]:
        warnings.append("FinSight my_config.yaml is unavailable.")
    if not checks["finsight_source_exists"]:
        warnings.append("FinSight source directory is unavailable or redirected.")
    if not checks["reference_template_exists"]:
        warnings.append("The pinned FinSight report template is unavailable or redirected.")
    if not checks["finsight_env_exists"]:
        warnings.append("FinSight .env is unavailable.")
    elif not checks["finsight_env_ready"]:
        warnings.append("FinSight .env is missing required model, embedding, VLM, or search credentials.")
    if not checks["runner_script_exists"]:
        warnings.append("The TickFlow FinSight runner is unavailable.")
    if not checks["pandoc_available"]:
        warnings.append("Pandoc is unavailable; DOCX rendering cannot start.")
    if not checks["word_detected"]:
        warnings.append("Microsoft Word was not detected; PDF rendering cannot start.")
    else:
        warnings.append("Microsoft Word must run in an interactive Windows session for PDF conversion.")

    # Keep the established response shape, but never disclose host filesystem paths.
    return {
        "ready": ready,
        "paths": {
            "finsight_root": "configured" if root is not None else "not-configured",
            "finsight_python": (
                "configured"
                if settings.finsight_python is not None
                else "auto-detected" if python_path is not None else "not-found"
            ),
            "base_config": "my_config.yaml",
            "runner_script": runner.name,
            "pandoc": "available" if pandoc_available else "not-found",
        },
        "checks": checks,
        "warnings": warnings,
    }


def list_runs(symbol: str | None = None) -> list[dict[str, Any]]:
    normalized_symbol = _normalize_symbol(symbol)[0] if symbol is not None else None
    runs = [_public_run(_sync_record(record), include_markdown=False) for record in _load_index()]
    if normalized_symbol is not None:
        runs = [run for run in runs if run.get("symbol") == normalized_symbol]
    runs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return runs


def get_run(run_id: str) -> dict[str, Any]:
    validated_id = _validate_run_id(run_id)
    record = _find_record(validated_id)
    if record is None:
        raise FileNotFoundError(f"Run not found: {validated_id}")
    return _public_run(_sync_record(record), include_markdown=True)


def create_run(
    *,
    symbol: str,
    name: str,
    collect_task_ids: list[str],
    analysis_task_ids: list[str],
) -> dict[str, Any]:
    if not integration_health()["ready"]:
        raise IntegrationUnavailableError("FinSight integration environment is not ready.")

    normalized_symbol, stock_code, market, market_label = _normalize_symbol(symbol)
    clean_name = _validate_target_name(name or normalized_symbol)
    collect, analysis = deep_report_tasks.resolve_task_prompts(
        collect_task_ids=collect_task_ids,
        analysis_task_ids=analysis_task_ids,
    )

    run_id = f"dsr_{int(time.time() * 1000)}_{_run_code(stock_code)}_{uuid.uuid4().hex[:8]}"
    run_dir = _run_dir(run_id)
    root = settings.finsight_root
    if root is None:  # Kept defensive even though integration_health checked it.
        raise IntegrationUnavailableError("FinSight root is not configured.")

    payload = {
        "run_id": run_id,
        "symbol": normalized_symbol,
        "stock_code": stock_code,
        "market": market,
        "market_label": market_label,
        "target_name": clean_name,
        "collect_task_ids": list(collect_task_ids),
        "analysis_task_ids": list(analysis_task_ids),
        "collect_tasks": collect,
        "analysis_tasks": analysis,
        "run_dir": str(run_dir),
        "output_root": str(run_dir / "finsight_output"),
        "base_config_path": str(root / "my_config.yaml"),
    }
    now = _now_iso()
    record = {
        "id": run_id,
        "symbol": normalized_symbol,
        "stock_code": stock_code,
        "market": market,
        "market_label": market_label,
        "name": clean_name,
        "collect_task_ids": list(collect_task_ids),
        "analysis_task_ids": list(analysis_task_ids),
        "collect_tasks": collect,
        "analysis_tasks": analysis,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Queued",
        "error": "",
        "report_title": "",
        "pdf_status": "pending",
        "pdf_error": "",
        "pid": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }

    with _INDEX_LOCK:
        index = _load_index()
        active_count = sum(
            _record_with_latest_status(item).get("status") in _ACTIVE_STATUSES for item in index
        )
        if active_count >= settings.finsight_max_concurrent:
            raise ConcurrentRunLimitError(
                f"At most {settings.finsight_max_concurrent} deep report run(s) may be active."
            )
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            _atomic_write_json(run_dir / "request.json", payload)
            _atomic_write_json(run_dir / "status.json", record)
            index.append(record)
            index.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            _save_index(index)
        except Exception:
            if run_dir.exists() and run_dir.resolve().parent == _runs_dir().resolve():
                shutil.rmtree(run_dir, ignore_errors=True)
            raise

    try:
        pid = _spawn_runner(
            request_path=run_dir / "request.json",
            status_path=run_dir / "status.json",
            log_path=run_dir / "run.log",
        )
        _atomic_write_json(run_dir / "runner.pid.json", {"pid": pid})
        _upsert_record({**record, "pid": pid})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to spawn FinSight runner for %s", run_id)
        failed = {
            **record,
            "status": "failed",
            "stage": "spawn_failed",
            "error": str(exc),
            "message": "The FinSight runner could not be started.",
            "finished_at": _now_iso(),
            "updated_at": _now_iso(),
            "pdf_status": "unavailable",
            "pdf_error": "Runner process could not be started.",
        }
        _atomic_write_json(run_dir / "status.json", failed)
        _upsert_record(failed)
        raise RunnerStartError("The FinSight runner could not be started.") from exc

    return get_run(run_id)


def cancel_run(run_id: str) -> dict[str, Any]:
    validated_id = _validate_run_id(run_id)
    record = _find_record(validated_id)
    if record is None:
        raise FileNotFoundError(f"Run not found: {validated_id}")

    synced = _sync_record(record)
    if synced.get("status") not in _ACTIVE_STATUSES:
        return _public_run(synced, include_markdown=True)

    pid = _coerce_pid(synced.get("pid")) or _read_runner_pid(validated_id)
    killed_pids = _terminate_runner_process(pid, validated_id) if pid is not None else []
    now = _now_iso()
    cancelled = {
        **synced,
        "status": "cancelled",
        "stage": "cancelled",
        "progress": int(synced.get("progress") or 0),
        "message": (
            "Report generation cancelled."
            if killed_pids
            else "Report marked cancelled. No matching active runner process was found."
        ),
        "error": "",
        "finished_at": now,
        "updated_at": now,
        "pdf_status": "unavailable",
        "pdf_error": "Report generation was cancelled by the user.",
        "cancelled_at": now,
        "killed_pids": killed_pids,
    }
    _atomic_write_json(_run_dir(validated_id) / "status.json", cancelled)
    _upsert_record(cancelled)
    return _public_run(cancelled, include_markdown=True)


def delete_run(run_id: str) -> bool:
    validated_id = _validate_run_id(run_id)
    record = _find_record(validated_id)
    if record is None:
        raise FileNotFoundError(f"Run not found: {validated_id}")

    synced = _sync_record(record)
    if synced.get("status") in _ACTIVE_STATUSES:
        raise ActiveRunError("Cannot delete a running report.")

    run_dir = _run_dir(validated_id)
    if run_dir.exists():
        resolved = run_dir.resolve()
        if resolved.parent != _runs_dir().resolve():
            raise RuntimeError("Unsafe run directory.")
        shutil.rmtree(resolved, ignore_errors=False)

    with _INDEX_LOCK:
        index = [item for item in _load_index() if item.get("id") != validated_id]
        _save_index(index)
    return True


def resolve_artifact(run_id: str, kind: str) -> tuple[Path, str, str]:
    validated_id = _validate_run_id(run_id)
    if kind not in _ARTIFACTS:
        raise ValueError(f"Unknown artifact kind: {kind}")
    record = _find_record(validated_id)
    if record is None:
        raise FileNotFoundError(f"Run not found: {validated_id}")

    filename, media_type, _ = _ARTIFACTS[kind]
    run_root = _run_dir(validated_id).resolve()
    path = run_root / filename
    resolved = path.resolve()
    if resolved.parent != run_root or not resolved.is_file() or resolved.stat().st_size <= 0:
        raise FileNotFoundError(f"Artifact not found: {kind}")

    public = _public_run(_sync_record(record), include_markdown=False)
    title = public.get("report_title") or public.get("name") or validated_id
    download_name = path.name
    if kind in {"markdown", "word", "pdf"}:
        download_name = f"{_safe_filename(str(title))}{path.suffix.lower()}"
    return resolved, media_type, download_name


def _public_run(record: dict[str, Any], *, include_markdown: bool) -> dict[str, Any]:
    run_id = _validate_run_id(str(record.get("id", "")))
    run_dir = _run_dir(run_id)
    artifacts = {kind: _artifact_meta(run_id, kind, run_dir) for kind in _ARTIFACTS}
    payload = {
        "id": run_id,
        "symbol": record.get("symbol", ""),
        "stock_code": record.get("stock_code", ""),
        "market": record.get("market", ""),
        "market_label": record.get("market_label", ""),
        "name": record.get("name", ""),
        "collect_task_ids": record.get("collect_task_ids", []),
        "analysis_task_ids": record.get("analysis_task_ids", []),
        "collect_tasks": record.get("collect_tasks", []),
        "analysis_tasks": record.get("analysis_tasks", []),
        "status": record.get("status", "queued"),
        "stage": record.get("stage", ""),
        "progress": int(record.get("progress") or 0),
        "message": _public_message(record),
        "error": _public_error(record),
        "pid": record.get("pid"),
        "report_title": record.get("report_title", ""),
        "pdf_status": record.get("pdf_status", "pending"),
        "pdf_error": record.get("pdf_error", ""),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "started_at": record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "artifacts": artifacts,
    }
    if include_markdown:
        markdown_path = run_dir / _ARTIFACTS["markdown"][0]
        payload["markdown_content"] = (
            markdown_path.read_text(encoding="utf-8", errors="replace")
            if _safe_regular_file(markdown_path, run_dir)
            else ""
        )
        # Full tracebacks remain available only through the explicit log attachment.
        payload["log_tail"] = ""
    return payload


def _artifact_meta(run_id: str, kind: str, run_dir: Path) -> dict[str, Any]:
    filename, _, label = _ARTIFACTS[kind]
    path = run_dir / filename
    available = _safe_regular_file(path, run_dir) and path.stat().st_size > 0
    return {
        "kind": kind,
        "label": label,
        "filename": filename,
        "available": available,
        "size": path.stat().st_size if available else 0,
        "download_url": (
            f"/api/stock-analysis/deep-reports/runs/{run_id}/download/{kind}"
            if available
            else ""
        ),
    }


def _sync_record(record: dict[str, Any]) -> dict[str, Any]:
    run_id = _validate_run_id(str(record.get("id", "")))
    latest = _record_with_latest_status(record)
    if latest.get("pid") is None:
        latest["pid"] = _read_runner_pid(run_id)
    _upsert_record(latest)
    return latest


def _record_with_latest_status(record: dict[str, Any]) -> dict[str, Any]:
    run_id = str(record.get("id", ""))
    if not _RUN_ID_RE.fullmatch(run_id):
        return dict(record)
    status_path = _run_dir(run_id) / "status.json"
    latest = dict(record)
    if status_path.exists() and not _safe_regular_file(status_path, status_path.parent):
        raise UnsafeRunPathError("Report status file is redirected outside its trusted run directory.")
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if isinstance(status, dict) and status.get("id", run_id) == run_id:
                latest.update(status)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Malformed status file for %s: %s", run_id, exc)
    return latest


def _spawn_runner(*, request_path: Path, status_path: Path, log_path: Path) -> int:
    python_path = _require_finsight_python()
    root = settings.finsight_root
    if not _unredirected_directory(root):
        raise IntegrationUnavailableError("FinSight root is not configured.")
    cmd = [
        str(python_path),
        str(_runner_script()),
        "--payload",
        str(request_path),
        "--status",
        str(status_path),
        "--runs-root",
        str(_runs_dir()),
    ]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    log_file = log_path.open("xb")
    try:
        process = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(root),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int(process.pid)
    finally:
        log_file.close()


def _normalize_symbol(symbol: str) -> tuple[str, str, str, str]:
    value = str(symbol or "").strip().upper()
    if not value or len(value) > 24 or any(character.isspace() for character in value):
        raise ValueError("symbol is invalid")
    match = _A_SHARE_RE.fullmatch(value)
    if match:
        return value, match.group("code"), "A", "A-share"
    match = _HK_RE.fullmatch(value)
    if match:
        return value, match.group("code"), "HK", "Hong Kong"
    match = _US_RE.fullmatch(value)
    if match:
        return value, match.group("code"), "US", "US"
    raise ValueError("symbol must be an A-share, Hong Kong, or US ticker")


def _validate_target_name(name: str) -> str:
    value = str(name or "").strip()
    if not value or len(value) > 100:
        raise ValueError("name must contain between 1 and 100 characters")
    if value in {".", ".."} or value.endswith((".", " ")):
        raise ValueError("name is not safe for a report directory")
    if any(character in '<>:"/\\|?*' or ord(character) < 32 for character in value):
        raise ValueError("name contains characters that are invalid in a Windows path")
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("name is reserved by Windows")
    return value


def _validate_run_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError("Run ID is invalid.")
    return value


def _run_code(stock_code: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", stock_code.lower()).strip("-")[:16]
    if not value:
        raise ValueError("symbol cannot be used in a run ID")
    return value


def _safe_regular_file(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved.parent == root.resolve() and resolved.is_file()
    except OSError:
        return False


def _public_message(record: dict[str, Any]) -> str:
    if record.get("status") == "failed":
        return "Report generation failed. Download the run log for details."
    return str(record.get("message", ""))[:500]


def _public_error(record: dict[str, Any]) -> str:
    if not record.get("error"):
        return ""
    return "Report generation failed. Download the run log for details."


def _find_record(run_id: str) -> dict[str, Any] | None:
    for item in _load_index():
        if item.get("id") == run_id:
            return item
    return None


def _read_runner_pid(run_id: str) -> int | None:
    path = _run_dir(run_id) / "runner.pid.json"
    if path.exists() and not _safe_regular_file(path, path.parent):
        raise UnsafeRunPathError("Report PID file is redirected outside its trusted run directory.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _coerce_pid(data.get("pid")) if isinstance(data, dict) else None


def _coerce_pid(value: Any) -> int | None:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _terminate_runner_process(pid: int, run_id: str) -> list[int]:
    try:
        process = psutil.Process(pid)
        if not _runner_process_matches(process, run_id):
            logger.warning("Refusing to terminate PID %s: it is not the expected report runner", pid)
            return []
        children = process.children(recursive=True)
        targets = [*children, process]
        for target in targets:
            try:
                target.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _, alive = psutil.wait_procs(targets, timeout=5)
        for target in alive:
            try:
                target.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        psutil.wait_procs(alive, timeout=3)
        return [target.pid for target in targets]
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return []


def _runner_process_matches(process: psutil.Process, run_id: str) -> bool:
    expected_payload = os.path.normcase(str((_run_dir(run_id) / "request.json").resolve()))
    try:
        command = process.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    normalized_args: set[str] = set()
    for argument in command:
        try:
            normalized_args.add(os.path.normcase(str(Path(argument).resolve())))
        except (OSError, ValueError):
            continue
    has_runner = any(Path(argument).name.casefold() == "finsight_job_runner.py" for argument in command)
    return has_runner and expected_payload in normalized_args


def _upsert_record(record: dict[str, Any]) -> None:
    run_id = _validate_run_id(str(record.get("id", "")))
    with _INDEX_LOCK:
        index = _load_index()
        for position, current in enumerate(index):
            if current.get("id") == run_id:
                index[position] = record
                break
        else:
            index.append(record)
        index.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        _save_index(index)


def _load_index() -> list[dict[str, Any]]:
    path = _index_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("index must be a list")
        records = [
            item
            for item in data
            if isinstance(item, dict) and _RUN_ID_RE.fullmatch(str(item.get("id", "")))
        ]
        if len(records) != len(data):
            logger.warning("Ignored invalid records in the deep report index")
        return records
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Malformed deep report index: %s", exc)
        return []


def _save_index(records: list[dict[str, Any]]) -> None:
    _atomic_write_json(_index_path(), records)


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


def _index_path() -> Path:
    return settings.data_dir / "user_data" / "ai_stock_deep_reports.json"


def _runs_dir() -> Path:
    path = settings.data_dir / "user_data" / "finsight_stock_reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_dir(run_id: str) -> Path:
    validated_id = _validate_run_id(run_id)
    root = _runs_dir().resolve()
    candidate = root / validated_id
    if candidate.exists() or candidate.is_symlink():
        resolved = candidate.resolve()
        if resolved.parent != root or resolved != candidate:
            raise UnsafeRunPathError("Report run directory is redirected outside its trusted root.")
    return candidate


def _runner_script() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "finsight_job_runner.py"


def _finsight_python() -> Path | None:
    configured = settings.finsight_python
    if configured is not None and configured.is_file():
        return configured
    root = settings.finsight_root
    if root is None:
        return None
    candidates = (
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "python.exe",
        root / ".venv" / "bin" / "python",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _require_finsight_python() -> Path:
    python_path = _finsight_python()
    if not _unredirected_executable(python_path):
        raise IntegrationUnavailableError(
            "FinSight Python environment not found. Set FINSIGHT_PYTHON or run setup-finsight.ps1."
        )
    return python_path


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_filename(value: str) -> str:
    cleaned = "".join("_" if character in '<>:"/\\|?*' or ord(character) < 32 else character for character in value)
    cleaned = cleaned.strip().rstrip(". ")[:100] or "report"
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _unredirected_directory(path: Path | None) -> bool:
    if path is None or not path.is_dir():
        return False
    try:
        return path.resolve() == path.absolute() and not path.is_symlink()
    except OSError:
        return False


def _unredirected_file(path: Path | None, parent: Path | None) -> bool:
    if path is None or parent is None or not path.is_file():
        return False
    try:
        return (
            path.resolve().parent == parent.resolve()
            and path.resolve() == path.absolute()
            and not path.is_symlink()
        )
    except OSError:
        return False


def _unredirected_executable(path: Path | None) -> bool:
    if path is None or not path.is_file():
        return False
    try:
        return path.resolve() == path.absolute() and not path.is_symlink()
    except OSError:
        return False


def _finsight_env_ready(path: Path | None) -> bool:
    if path is None or not path.is_file():
        return False
    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_value = value.strip().strip('"').strip("'")
            values[key.strip()] = normalized_value
    except OSError:
        return False
    return all(any(values.get(key, "").strip() for key in alternatives) for alternatives in _FINSIGHT_ENV_GROUPS)


def _word_detected() -> bool:
    if os.name != "nt":
        return False
    if shutil.which("winword"):
        return True
    for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
        program_files = os.environ.get(environment_name)
        if not program_files:
            continue
        for relative in (
            Path("Microsoft Office") / "root" / "Office16" / "WINWORD.EXE",
            Path("Microsoft Office") / "Office16" / "WINWORD.EXE",
        ):
            if (Path(program_files) / relative).is_file():
                return True
    try:
        import winreg

        key_paths = (
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE",
        )
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for key_path in key_paths:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                    if value and Path(value).is_file():
                        return True
                except OSError:
                    continue
    except ImportError:
        return False
    return False
