from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

import polars as pl
import pyarrow.parquet as pq


CANONICAL_FILE_RE = re.compile(r"^(?P<code>\d{6})_(?P<market>SZ|SH|BJ)\.parquet$")
COLUMNS = ["ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"]
CANONICAL_SCHEMA = pl.Schema({
    "ts_code": pl.String,
    "trade_time": pl.String,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "vol": pl.Int64,
    "amount": pl.Float64,
})


@dataclass(frozen=True)
class SourceItem:
    file_name: str
    current_path: Path
    history_path: Path | None
    expected_symbol: str


@dataclass(frozen=True)
class Inventory:
    items: list[SourceItem]
    rejected: list[str]


@dataclass(frozen=True)
class NormalizationResult:
    file_name: str
    expected_symbol: str
    coverage_state: str
    current_rows: int
    history_rows: int | None
    output_rows: int
    output_bytes: int
    first_timestamp: str | None
    last_timestamp: str | None
    duplicate_rows_removed: int
    source_fingerprints: dict[str, dict[str, Any] | None]
    warnings: list[str]
    status: str = "success"

    def to_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["recorded_at"] = datetime.now().isoformat(timespec="seconds")
        return record


def fingerprint(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def inventory_sources(current_dir: Path, history_dir: Path) -> Inventory:
    current_files = sorted(current_dir.glob("*.parquet"), key=lambda path: path.name)
    rejected = [path.name for path in current_files if CANONICAL_FILE_RE.fullmatch(path.name) is None]
    items: list[SourceItem] = []
    for current_path in current_files:
        match = CANONICAL_FILE_RE.fullmatch(current_path.name)
        if match is None:
            continue
        history_path = history_dir / current_path.name
        items.append(SourceItem(
            file_name=current_path.name,
            current_path=current_path,
            history_path=history_path if history_path.is_file() else None,
            expected_symbol=f"{match.group('code')}.{match.group('market')}",
        ))
    return Inventory(items=items, rejected=sorted(rejected))


def _canonical_frame(path: Path) -> pl.DataFrame:
    return pl.read_parquet(path).select(COLUMNS).with_columns(
        pl.col("ts_code").cast(pl.String),
        pl.col("trade_time").cast(pl.String),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("vol").cast(pl.Int64),
        pl.col("amount").cast(pl.Float64),
    )


def _validate_identity(frame: pl.DataFrame, expected_symbol: str) -> None:
    if frame.is_empty():
        return
    symbols = frame.get_column("ts_code").unique().to_list()
    if symbols != [expected_symbol]:
        raise ValueError(f"expected {expected_symbol}, found {symbols}")


def _coverage_state(
    *,
    history_path: Path | None,
    history_corrupt: bool,
    history_rows: int | None,
    current_rows: int,
) -> str:
    if history_corrupt:
        return "current_only_history_corrupt"
    if history_path is None:
        return "current_only_no_history" if current_rows else "empty_current_only"
    if history_rows and current_rows:
        return "merged_history_current"
    if history_rows and not current_rows:
        return "history_only_current_empty"
    if not history_rows and current_rows:
        return "current_only_history_empty"
    return "empty_both"


def _validate_output(path: Path, expected_rows: int | None = None) -> None:
    metadata = pq.ParquetFile(path).metadata
    schema = pl.read_parquet_schema(path)
    if schema != CANONICAL_SCHEMA:
        raise ValueError(f"noncanonical output schema for {path.name}: {schema}")
    if expected_rows is not None and metadata.num_rows != expected_rows:
        raise ValueError(
            f"output row mismatch for {path.name}: {metadata.num_rows} != {expected_rows}"
        )


def normalize_item(item: SourceItem, output_dir: Path) -> NormalizationResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    current_fingerprint = fingerprint(item.current_path)
    history_fingerprint = fingerprint(item.history_path)
    current = _canonical_frame(item.current_path)
    _validate_identity(current, item.expected_symbol)

    warnings: list[str] = []
    history: pl.DataFrame | None = None
    history_corrupt = False
    if item.history_path is not None:
        try:
            history = _canonical_frame(item.history_path)
            _validate_identity(history, item.expected_symbol)
        except Exception as exc:  # noqa: BLE001 - history degradation is recorded
            history_corrupt = True
            warnings.append(f"history unreadable: {type(exc).__name__}: {exc}")

    frames: list[pl.DataFrame] = []
    if history is not None:
        frames.append(history.with_columns(pl.lit(0).alias("_source_rank")))
    frames.append(current.with_columns(pl.lit(1).alias("_source_rank")))
    source_rows = sum(frame.height for frame in frames)
    merged = (
        pl.concat(frames, how="vertical")
        .sort(["trade_time", "_source_rank"])
        .unique(subset=["ts_code", "trade_time"], keep="last", maintain_order=True)
        .drop("_source_rank")
        .sort("trade_time")
    )
    _validate_identity(merged, item.expected_symbol)

    output_path = output_dir / item.file_name
    partial_path = output_dir / f"{item.file_name}.partial"
    if partial_path.exists():
        partial_path.unlink()
    try:
        merged.write_parquet(partial_path, compression="zstd", statistics=True)
        _validate_output(partial_path, merged.height)
        os.replace(partial_path, output_path)
    finally:
        if partial_path.exists():
            partial_path.unlink()

    history_rows = history.height if history is not None else None
    return NormalizationResult(
        file_name=item.file_name,
        expected_symbol=item.expected_symbol,
        coverage_state=_coverage_state(
            history_path=item.history_path,
            history_corrupt=history_corrupt,
            history_rows=history_rows,
            current_rows=current.height,
        ),
        current_rows=current.height,
        history_rows=history_rows,
        output_rows=merged.height,
        output_bytes=output_path.stat().st_size,
        first_timestamp=merged.item(0, "trade_time") if merged.height else None,
        last_timestamp=merged.item(-1, "trade_time") if merged.height else None,
        duplicate_rows_removed=source_rows - merged.height,
        source_fingerprints={
            "current": current_fingerprint,
            "history": history_fingerprint,
        },
        warnings=warnings,
    )


def append_state(state_path: Path, record: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_latest_state(state_path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not state_path.is_file():
        return latest
    with state_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            latest[str(record["file_name"])] = record
    return latest


def should_skip(item: SourceItem, record: dict[str, Any] | None, output_dir: Path) -> bool:
    if not record or record.get("status") != "success":
        return False
    expected_fingerprints = {
        "current": fingerprint(item.current_path),
        "history": fingerprint(item.history_path),
    }
    if record.get("source_fingerprints") != expected_fingerprints:
        return False
    output_path = output_dir / item.file_name
    if not output_path.is_file():
        return False
    try:
        _validate_output(output_path, int(record["output_rows"]))
    except Exception:
        return False
    return True


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, path)


def write_manifests(
    output_dir: Path,
    inventory: Inventory,
    latest: dict[str, dict[str, Any]],
) -> tuple[Path, Path]:
    records = [latest[item.file_name] for item in inventory.items if item.file_name in latest]
    successes = [record for record in records if record.get("status") == "success"]
    failures = [record for record in records if record.get("status") != "success"]
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "format": "tickflow-per-symbol-minute-parquet-v1",
        "columns": COLUMNS,
        "canonical_file_count": len(inventory.items),
        "output_file_count": len(successes),
        "failure_count": len(failures),
        "total_output_rows": sum(int(record.get("output_rows", 0)) for record in successes),
        "total_output_bytes": sum(int(record.get("output_bytes", 0)) for record in successes),
        "coverage_state_counts": dict(Counter(
            str(record.get("coverage_state")) for record in successes
        )),
        "coverage": successes,
        "failures": failures,
    }
    rejected = {
        "generated_at": manifest["generated_at"],
        "rejected_inputs": inventory.rejected,
    }
    manifest_path = output_dir / "manifest.json"
    rejected_path = output_dir / "rejected_inputs.json"
    _write_json_atomic(manifest_path, manifest)
    _write_json_atomic(rejected_path, rejected)
    return manifest_path, rejected_path


def _failure_record(item: SourceItem, exc: Exception) -> dict[str, Any]:
    return {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "file_name": item.file_name,
        "expected_symbol": item.expected_symbol,
        "status": "failure",
        "error": f"{type(exc).__name__}: {exc}",
        "source_fingerprints": {
            "current": fingerprint(item.current_path),
            "history": fingerprint(item.history_path),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize all local minute Parquet data")
    parser.add_argument("--current-dir", type=Path, required=True)
    parser.add_argument("--history-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    inventory = inventory_sources(args.current_dir, args.history_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.output_dir / ".normalization" / "state.jsonl"
    latest = load_latest_state(state_path)
    items = inventory.items[:args.limit] if args.limit is not None else inventory.items
    failures = 0
    for index, item in enumerate(items, 1):
        if should_skip(item, latest.get(item.file_name), args.output_dir):
            print(f"[{index}/{len(items)}] skip {item.file_name}", flush=True)
            continue
        try:
            result = normalize_item(item, args.output_dir)
            record = result.to_record()
            append_state(state_path, record)
            latest[item.file_name] = record
            print(
                f"[{index}/{len(items)}] {item.file_name} rows={result.output_rows} "
                f"state={result.coverage_state}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 - continue remaining symbols
            failures += 1
            record = _failure_record(item, exc)
            append_state(state_path, record)
            latest[item.file_name] = record
            print(f"[{index}/{len(items)}] ERROR {item.file_name}: {record['error']}", file=sys.stderr, flush=True)

    write_manifests(args.output_dir, inventory, latest)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
