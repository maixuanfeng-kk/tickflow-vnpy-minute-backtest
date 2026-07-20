from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import stock_analysis
from app.config import settings
from app.scripts import finsight_job_runner
from app.services import deep_report_tasks, deep_stock_reports


@pytest.fixture
def report_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    data_dir = tmp_path / "data"
    finsight_root = tmp_path / "private" / "finsight"
    finsight_root.mkdir(parents=True)
    (finsight_root / "my_config.yaml").write_text("target_type: financial_company\n", encoding="utf-8")
    template = finsight_root / "src" / "template" / "report_template.docx"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"template")
    (finsight_root / ".env").write_text(
        "\n".join(
            (
                "DS_MODEL_NAME=model",
                "DS_BASE_URL=https://example.invalid/v1",
                "DS_API_KEY=secret",
                "VLM_MODEL_NAME=vlm",
                "VLM_BASE_URL=https://example.invalid/v1",
                "VLM_API_KEY=secret",
                "EMBEDDING_MODEL_NAME=embedding",
                "EMBEDDING_BASE_URL=https://example.invalid/v1",
                "EMBEDDING_API_KEY=secret",
                "SERPER_API_KEY=secret",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    python_path = finsight_root / ".venv" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"python")
    pandoc_path = finsight_root / ".venv" / "Library" / "bin" / "pandoc.exe"
    pandoc_path.parent.mkdir(parents=True)
    pandoc_path.write_bytes(b"pandoc")

    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "finsight_root", finsight_root)
    monkeypatch.setattr(settings, "finsight_python", python_path)
    monkeypatch.setattr(settings, "finsight_max_concurrent", 1)
    monkeypatch.setattr(deep_stock_reports, "_word_detected", lambda: True)
    return {"data_dir": data_dir, "finsight_root": finsight_root, "python": python_path}


def _task_ids() -> tuple[list[str], list[str]]:
    return ["collect-financial-statements"], ["analysis-company-history"]


def _create_run(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(deep_stock_reports, "_spawn_runner", lambda **_: 4242)
    collect_ids, analysis_ids = _task_ids()
    return deep_stock_reports.create_run(
        symbol="601991.SH",
        name="大唐发电",
        collect_task_ids=collect_ids,
        analysis_task_ids=analysis_ids,
    )


def test_health_does_not_disclose_absolute_paths(report_environment: dict[str, Path]) -> None:
    health = deep_stock_reports.integration_health()

    assert health["ready"] is True
    serialized = json.dumps(health, ensure_ascii=False)
    assert str(report_environment["finsight_root"]) not in serialized
    assert str(report_environment["python"]) not in serialized
    assert health["paths"]["finsight_root"] == "configured"


def test_health_blocks_missing_credentials_and_word(
    report_environment: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = report_environment["finsight_root"] / ".env"
    env_path.write_text("DS_MODEL_NAME=model\nDS_API_KEY=\n", encoding="utf-8")
    health = deep_stock_reports.integration_health()
    assert health["ready"] is False
    assert health["checks"]["finsight_env_ready"] is False

    monkeypatch.setattr(deep_stock_reports, "_word_detected", lambda: False)
    health = deep_stock_reports.integration_health()
    assert health["checks"]["word_detected"] is False
    assert health["ready"] is False


def test_task_catalog_is_seeded_and_written_atomically(report_environment: dict[str, Path]) -> None:
    catalog = deep_report_tasks.get_catalog(active_only=True)
    path = report_environment["data_dir"] / "user_data" / "deep_report_task_catalog.json"

    assert catalog["collect_tasks"]
    assert catalog["analysis_tasks"]
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))


def test_task_catalog_rejects_duplicates_and_unsafe_ids(report_environment: dict[str, Path]) -> None:
    with pytest.raises(ValueError, match="Duplicate collect"):
        deep_report_tasks.resolve_task_prompts(
            collect_task_ids=["collect-financial-statements", "collect-financial-statements"],
            analysis_task_ids=["analysis-company-history"],
        )

    with pytest.raises(ValueError, match="Task ID is invalid"):
        deep_report_tasks.delete_task("../catalog")


def test_create_run_validates_symbol_and_windows_safe_name(
    report_environment: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deep_stock_reports, "_spawn_runner", lambda **_: 4242)
    collect_ids, analysis_ids = _task_ids()

    with pytest.raises(ValueError, match="symbol"):
        deep_stock_reports.create_run(
            symbol="../../secret",
            name="safe",
            collect_task_ids=collect_ids,
            analysis_task_ids=analysis_ids,
        )
    with pytest.raises(ValueError, match="Windows path"):
        deep_stock_reports.create_run(
            symbol="601991.SH",
            name="../escape",
            collect_task_ids=collect_ids,
            analysis_task_ids=analysis_ids,
        )
    with pytest.raises(ValueError, match="reserved"):
        deep_stock_reports.create_run(
            symbol="601991.SH",
            name="CON.txt",
            collect_task_ids=collect_ids,
            analysis_task_ids=analysis_ids,
        )


def test_default_concurrency_limit_blocks_second_active_run(
    report_environment: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _create_run(monkeypatch)

    with pytest.raises(deep_stock_reports.ConcurrentRunLimitError):
        _create_run(monkeypatch)

    status_path = deep_stock_reports._run_dir(first["id"]) / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status.update({"status": "succeeded", "stage": "finished", "progress": 100})
    deep_stock_reports._atomic_write_json(status_path, status)
    second = _create_run(monkeypatch)
    assert second["id"] != first["id"]


def test_cancel_verifies_runner_then_delete_stays_inside_run_root(
    report_environment: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _create_run(monkeypatch)
    terminated: list[tuple[int, str]] = []

    def terminate(pid: int, run_id: str) -> list[int]:
        terminated.append((pid, run_id))
        return [pid]

    monkeypatch.setattr(deep_stock_reports, "_terminate_runner_process", terminate)
    cancelled = deep_stock_reports.cancel_run(run["id"])
    run_dir = deep_stock_reports._run_dir(run["id"])

    assert cancelled["status"] == "cancelled"
    assert terminated == [(4242, run["id"])]
    assert deep_stock_reports.delete_run(run["id"]) is True
    assert not run_dir.exists()
    with pytest.raises(FileNotFoundError):
        deep_stock_reports.delete_run(run["id"])
    with pytest.raises(ValueError, match="Run ID"):
        deep_stock_reports.delete_run("../outside")


def test_artifact_resolution_rejects_unknown_kind(
    report_environment: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _create_run(monkeypatch)
    with pytest.raises(ValueError, match="Unknown artifact kind"):
        deep_stock_reports.resolve_artifact(run["id"], "../../request")


def test_artifact_resolution_rejects_redirected_run_directory(
    report_environment: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _create_run(monkeypatch)
    monkeypatch.setattr(deep_stock_reports, "_terminate_runner_process", lambda *_: [])
    deep_stock_reports.cancel_run(run["id"])
    run_dir = deep_stock_reports._run_dir(run["id"])
    shutil.rmtree(run_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.pdf").write_bytes(b"outside")
    try:
        run_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this Windows host")

    with pytest.raises(deep_stock_reports.UnsafeRunPathError):
        deep_stock_reports.resolve_artifact(run["id"], "pdf")


def test_api_maps_environment_and_concurrency_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    request = stock_analysis.CreateDeepReportRequest(
        symbol="601991.SH",
        collect_task_ids=["collect-financial-statements"],
        analysis_task_ids=["analysis-company-history"],
    )
    monkeypatch.setattr(
        deep_stock_reports,
        "create_run",
        lambda **_: (_ for _ in ()).throw(deep_stock_reports.IntegrationUnavailableError("not ready")),
    )
    with pytest.raises(HTTPException) as unavailable:
        stock_analysis.create_deep_report_run(request)
    assert unavailable.value.status_code == 503

    monkeypatch.setattr(
        deep_stock_reports,
        "create_run",
        lambda **_: (_ for _ in ()).throw(deep_stock_reports.ConcurrentRunLimitError("busy")),
    )
    with pytest.raises(HTTPException) as conflict:
        stock_analysis.create_deep_report_run(request)
    assert conflict.value.status_code == 409


def test_runner_status_write_is_atomic_and_preserves_cancel(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    finsight_job_runner.write_status(status_path, {"status": "queued", "progress": 0})
    finsight_job_runner.write_status(status_path, {"status": "cancelled", "progress": 10})
    finsight_job_runner.write_status(status_path, {"status": "running", "progress": 50})

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "cancelled"
    assert status["progress"] == 10
    assert not list(tmp_path.glob(".status.json.*.tmp"))


def test_runner_rejects_output_path_outside_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "dsr_1000000000000_601991_abcdef12"
    run_dir.mkdir(parents=True)
    payload_path = run_dir / "request.json"
    status_path = run_dir / "status.json"
    config_path = tmp_path / "finsight" / "my_config.yaml"
    config_path.parent.mkdir()
    (config_path.parent / "src").mkdir()
    config_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(config_path.parent)
    payload = {
        "run_id": run_dir.name,
        "symbol": "601991.SH",
        "stock_code": "601991",
        "market": "A",
        "market_label": "A-share",
        "target_name": "大唐发电",
        "collect_tasks": ["财务报表"],
        "analysis_tasks": ["盈利能力"],
        "run_dir": str(run_dir),
        "output_root": str(tmp_path / "outside"),
        "base_config_path": str(config_path),
    }

    with pytest.raises(ValueError, match="output_root"):
        finsight_job_runner.validate_job_paths(
            payload,
            payload_path=payload_path,
            status_path=status_path,
            runs_root=runs_root,
        )


def test_runner_rejects_config_from_a_different_source_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "dsr_1000000000000_601991_abcdef12"
    run_dir.mkdir(parents=True)
    trusted_root = tmp_path / "trusted-finsight"
    (trusted_root / "src").mkdir(parents=True)
    (trusted_root / "my_config.yaml").write_text("{}\n", encoding="utf-8")
    untrusted_root = tmp_path / "other-finsight"
    untrusted_root.mkdir()
    untrusted_config = untrusted_root / "my_config.yaml"
    untrusted_config.write_text("{}\n", encoding="utf-8")
    monkeypatch.chdir(trusted_root)
    payload = {
        "run_id": run_dir.name,
        "symbol": "601991.SH",
        "stock_code": "601991",
        "market": "A",
        "market_label": "A-share",
        "target_name": "大唐发电",
        "collect_tasks": ["财务报表"],
        "analysis_tasks": ["盈利能力"],
        "run_dir": str(run_dir),
        "output_root": str(run_dir / "finsight_output"),
        "base_config_path": str(untrusted_config),
    }

    with pytest.raises(ValueError, match="base configuration"):
        finsight_job_runner.validate_job_paths(
            payload,
            payload_path=run_dir / "request.json",
            status_path=run_dir / "status.json",
            runs_root=runs_root,
        )
