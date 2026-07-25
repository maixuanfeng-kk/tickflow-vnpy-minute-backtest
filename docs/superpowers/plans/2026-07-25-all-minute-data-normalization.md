# All-Minute-Data Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a resumable tool that converts all 5,865 canonical source filenames into a durable TickFlow-ready minute Parquet library.

**Architecture:** A standalone backend script inventories the current directory as the canonical universe, conditionally merges matching history, writes one atomic output per symbol, and appends source-fingerprinted state records. A final manifest is compiled from validated outputs; corrupt history is warned and skipped, while unreadable current input is a per-symbol failure.

**Tech Stack:** Python 3.11, Polars, PyArrow, pytest, JSONL state, Parquet/Zstandard.

---

### Task 1: Define inventory and canonical schema behavior

**Files:**
- Create: `backend/tests/scripts/test_normalize_all_minute_data.py`
- Create: `backend/scripts/normalize_all_minute_data.py`

- [ ] **Step 1: Write failing inventory tests**

Create temporary current/history directories with canonical files, `T600018_SH.parquet`, an empty canonical file, and a history-only file. Assert `inventory_sources()` returns only current canonical filenames in sorted order, reports the anomaly as rejected, and does not add history-only names.

```python
def test_inventory_uses_current_canonical_universe(tmp_path):
    current, history = source_dirs(tmp_path)
    write_minute(current / "000001_SZ.parquet", [minute("000001.SZ", "2026-01-02 09:30:00")])
    write_empty(current / "000003_SZ.parquet")
    write_empty(current / "T600018_SH.parquet")
    write_empty(history / "000001_SZ.parquet")
    write_empty(history / "600000_SH.parquet")

    inventory = inventory_sources(current, history)

    assert [item.file_name for item in inventory.items] == ["000001_SZ.parquet", "000003_SZ.parquet"]
    assert inventory.rejected == ["T600018_SH.parquet"]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
$env:PYTHONPATH='D:\quant\tickflow-stock-panel\backend\.venv\Lib\site-packages'
D:\Anaconda3\python.exe -m pytest backend\tests\scripts\test_normalize_all_minute_data.py -q
```

Expected: collection fails because `backend.scripts.normalize_all_minute_data` does not exist.

- [ ] **Step 3: Implement inventory and schema constants**

Implement `CANONICAL_FILE_RE`, `COLUMNS`, `SCHEMA`, `SourceItem`, `Inventory`, `fingerprint()`, and `inventory_sources()`. The current directory defines the output universe; history is matched by exact filename only.

- [ ] **Step 4: Run the inventory test and verify GREEN**

Expected: inventory test passes.

### Task 2: Normalize one symbol safely

**Files:**
- Modify: `backend/tests/scripts/test_normalize_all_minute_data.py`
- Modify: `backend/scripts/normalize_all_minute_data.py`

- [ ] **Step 1: Write failing merge tests**

Add tests proving `normalize_item()`:

- merges history and current rows;
- sorts by `trade_time`;
- removes duplicate `(ts_code, trade_time)` rows with current winning;
- emits canonical typed schema for empty inputs;
- uses current-only data and warning state when history is corrupt;
- fails when row `ts_code` disagrees with the filename.

```python
def test_normalize_item_merges_sorts_and_prefers_current(tmp_path):
    item = make_overlapping_item(tmp_path)
    result = normalize_item(item, tmp_path / "out")
    output = pl.read_parquet(tmp_path / "out" / item.file_name)
    assert output["trade_time"].to_list() == sorted(output["trade_time"].to_list())
    assert output.filter(pl.col("trade_time") == "2025-07-16 09:30:00").item(0, "close") == 12.0
    assert result.duplicate_rows_removed == 1
```

- [ ] **Step 2: Run tests and verify RED**

Expected: failures because `normalize_item()` is absent.

- [ ] **Step 3: Implement minimal normalization**

Read readable sources, cast the eight columns, concatenate with source rank, validate identity, sort/deduplicate, write `<name>.partial`, validate the footer/schema/row count, and call `os.replace()` for the final path. Return source rows, output rows, duplicate count, bounds, coverage state, warnings, and fingerprints.

- [ ] **Step 4: Run tests and verify GREEN**

Expected: all normalization behavior tests pass.

### Task 3: Add source-fingerprinted resume and manifests

**Files:**
- Modify: `backend/tests/scripts/test_normalize_all_minute_data.py`
- Modify: `backend/scripts/normalize_all_minute_data.py`

- [ ] **Step 1: Write failing resume tests**

Assert that matching source fingerprints plus a valid output skip processing, changed source size/mtime forces regeneration, a stale `.partial` file is replaced, latest JSONL state wins, and final manifests account for every item/rejection/failure.

- [ ] **Step 2: Run tests and verify RED**

Expected: resume/manifest assertions fail because state handling is absent.

- [ ] **Step 3: Implement state and CLI**

Add:

```text
--current-dir
--history-dir
--output-dir
--limit (test/smoke only)
```

Use `.normalization/state.jsonl` and `.normalization/run.log`. Append and flush one JSON record after each validated final output. Generate `manifest.json` and `rejected_inputs.json` from the latest record per canonical filename. Continue after per-symbol failures and exit nonzero when any current input cannot produce valid output.

- [ ] **Step 4: Run focused tests and the existing minute tests**

Run the new test module, then:

```powershell
$env:PYTHONPATH='D:\quant\tickflow-stock-panel\backend\.venv\Lib\site-packages'
D:\Anaconda3\python.exe -m pytest backend\tests\backtest\test_minute_portfolio.py -q
```

Expected: all tests pass.

### Task 4: Smoke test and run full conversion

**Files:**
- Output: `F:\quant\data\minute_1min_tickflow_merged\`

- [ ] **Step 1: Run a five-file smoke conversion**

Use `--limit 5`, then verify canonical schemas, readable footers, state records, and resume skips. Remove only the task-owned smoke output after verification.

- [ ] **Step 2: Start the full sequential run**

Run the normalizer with the approved source/output directories and redirect stdout/stderr to `.normalization/run.log`. Use a hidden background process so independent workbook work can proceed, but monitor until completion.

- [ ] **Step 3: Resume if interrupted**

Run the exact command again. Expected: validated unchanged files skip and processing resumes from missing/stale items.

### Task 5: Verify the full library

**Files:**
- Inspect: `F:\quant\data\minute_1min_tickflow_merged\manifest.json`
- Inspect: `F:\quant\data\minute_1min_tickflow_merged\rejected_inputs.json`

- [ ] **Step 1: Run complete metadata verification**

Assert 5,865 canonical output files, readable footers, canonical schemas, manifest/output row and byte totals, no `.partial` files, one rejected input, and explicit `603838.SH` history-corruption warning.

- [ ] **Step 2: Run TickFlow compatibility samples**

Load deterministic representatives across SZ/SH/BJ and merged/current-only/empty/corrupt-history states with `LocalMinuteParquetRepository`. Assert parsed internal columns and datetime types.

- [ ] **Step 3: Record final counts and duration**

Update the task planning files with output rows, bytes, coverage-state counts, failures, and verification results.

