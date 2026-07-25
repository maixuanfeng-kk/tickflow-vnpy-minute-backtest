from __future__ import annotations

import json
import os
from pathlib import Path

import polars as pl
import pytest

from scripts.normalize_all_minute_data import (
    CANONICAL_SCHEMA,
    append_state,
    inventory_sources,
    load_latest_state,
    normalize_item,
    should_skip,
    write_manifests,
)


COLUMNS = ["ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"]


def minute(symbol: str, trade_time: str, close: float = 10.0) -> dict:
    return {
        "ts_code": symbol,
        "trade_time": trade_time,
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "vol": 100,
        "amount": close * 100,
    }


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).select(COLUMNS).write_parquet(path)


def write_empty(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({column: [] for column in COLUMNS}).write_parquet(path)


def source_dirs(tmp_path: Path) -> tuple[Path, Path]:
    current = tmp_path / "current"
    history = tmp_path / "history"
    current.mkdir()
    history.mkdir()
    return current, history


def test_inventory_uses_current_canonical_universe(tmp_path: Path) -> None:
    current, history = source_dirs(tmp_path)
    write_rows(current / "000001_SZ.parquet", [minute("000001.SZ", "2026-01-02 09:30:00")])
    write_empty(current / "000003_SZ.parquet")
    write_empty(current / "T600018_SH.parquet")
    write_empty(history / "000001_SZ.parquet")
    write_empty(history / "600000_SH.parquet")

    inventory = inventory_sources(current, history)

    assert [item.file_name for item in inventory.items] == ["000001_SZ.parquet", "000003_SZ.parquet"]
    assert inventory.rejected == ["T600018_SH.parquet"]
    assert inventory.items[0].history_path == history / "000001_SZ.parquet"
    assert inventory.items[1].history_path is None


def test_normalize_item_merges_sorts_and_prefers_current(tmp_path: Path) -> None:
    current, history = source_dirs(tmp_path)
    write_rows(history / "000001_SZ.parquet", [
        minute("000001.SZ", "2025-07-16 09:30:00", 9.0),
        minute("000001.SZ", "2025-07-15 09:30:00", 8.0),
    ])
    write_rows(current / "000001_SZ.parquet", [
        minute("000001.SZ", "2025-07-16 09:30:00", 12.0),
        minute("000001.SZ", "2025-07-17 09:30:00", 13.0),
    ])
    item = inventory_sources(current, history).items[0]

    result = normalize_item(item, tmp_path / "out")
    output = pl.read_parquet(tmp_path / "out" / item.file_name)

    assert output["trade_time"].to_list() == sorted(output["trade_time"].to_list())
    assert output.filter(pl.col("trade_time") == "2025-07-16 09:30:00").item(0, "close") == 12.0
    assert result.output_rows == 3
    assert result.duplicate_rows_removed == 1
    assert result.coverage_state == "merged_history_current"
    assert not (tmp_path / "out" / f"{item.file_name}.partial").exists()


def test_normalize_item_writes_typed_empty_output(tmp_path: Path) -> None:
    current, history = source_dirs(tmp_path)
    write_empty(current / "000003_SZ.parquet")
    item = inventory_sources(current, history).items[0]

    result = normalize_item(item, tmp_path / "out")
    output = pl.read_parquet(tmp_path / "out" / item.file_name)

    assert output.schema == CANONICAL_SCHEMA
    assert output.height == 0
    assert result.coverage_state == "empty_current_only"


def test_normalize_item_keeps_current_when_history_is_corrupt(tmp_path: Path) -> None:
    current, history = source_dirs(tmp_path)
    write_rows(current / "603838_SH.parquet", [minute("603838.SH", "2026-01-02 09:30:00")])
    (history / "603838_SH.parquet").write_bytes(b"not parquet")
    item = inventory_sources(current, history).items[0]

    result = normalize_item(item, tmp_path / "out")

    assert result.coverage_state == "current_only_history_corrupt"
    assert result.output_rows == 1
    assert result.warnings and "history" in result.warnings[0].lower()


def test_normalize_item_rejects_symbol_mismatch(tmp_path: Path) -> None:
    current, history = source_dirs(tmp_path)
    write_rows(current / "000001_SZ.parquet", [minute("000002.SZ", "2026-01-02 09:30:00")])
    item = inventory_sources(current, history).items[0]

    with pytest.raises(ValueError, match="000001.SZ"):
        normalize_item(item, tmp_path / "out")


def test_resume_requires_matching_fingerprints_and_valid_output(tmp_path: Path) -> None:
    current, history = source_dirs(tmp_path)
    write_rows(current / "000001_SZ.parquet", [minute("000001.SZ", "2026-01-02 09:30:00")])
    item = inventory_sources(current, history).items[0]
    output_dir = tmp_path / "out"
    result = normalize_item(item, output_dir)
    state_path = output_dir / ".normalization" / "state.jsonl"
    append_state(state_path, result.to_record())
    latest = load_latest_state(state_path)

    assert should_skip(item, latest[item.file_name], output_dir)

    stat = item.current_path.stat()
    os.utime(item.current_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    changed_item = inventory_sources(current, history).items[0]
    assert not should_skip(changed_item, latest[item.file_name], output_dir)


def test_latest_state_wins_and_manifests_account_for_inventory(tmp_path: Path) -> None:
    current, history = source_dirs(tmp_path)
    write_rows(current / "000001_SZ.parquet", [minute("000001.SZ", "2026-01-02 09:30:00")])
    write_empty(current / "000003_SZ.parquet")
    write_empty(current / "T600018_SH.parquet")
    inventory = inventory_sources(current, history)
    output_dir = tmp_path / "out"
    state_path = output_dir / ".normalization" / "state.jsonl"
    for item in inventory.items:
        result = normalize_item(item, output_dir)
        append_state(state_path, result.to_record())
    replacement = dict(load_latest_state(state_path)["000001_SZ.parquet"])
    replacement["warnings"] = ["latest"]
    append_state(state_path, replacement)

    latest = load_latest_state(state_path)
    manifest_path, rejected_path = write_manifests(output_dir, inventory, latest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rejected = json.loads(rejected_path.read_text(encoding="utf-8"))

    assert latest["000001_SZ.parquet"]["warnings"] == ["latest"]
    assert manifest["canonical_file_count"] == 2
    assert manifest["output_file_count"] == 2
    assert manifest["total_output_rows"] == 1
    assert rejected["rejected_inputs"] == ["T600018_SH.parquet"]
