# Local Minute CSV Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing minute-K API and stock minute chart read 1-minute OHLCV data from `D:\quant\data` CSV files without remote TickFlow access.

**Architecture:** Add a focused `LocalMinuteCsvSource` for symbol-to-file mapping, Chinese CSV parsing, latest-date lookup, and date filtering. Keep the existing project Parquet repository as the first read path, then use the source as a read-only fallback. Select `local_csv` as the default minute provider and explicitly prevent local mode from invoking remote minute sync or history-extension endpoints.

**Tech Stack:** FastAPI, Polars, Pydantic Settings, pytest, React/TypeScript, TanStack Query.

---

## File map

- Create `backend/app/data_providers/local_minute_csv.py`: pure local CSV source and provider wrapper; no writes to the external directory.
- Modify `backend/app/config.py`: expose `minute_csv_dir` with `MINUTE_CSV_DIR` override.
- Modify `backend/app/services/preferences.py`: allow `local_csv` and make it the default minute provider.
- Modify `backend/app/data_providers/registry.py`: register the local provider.
- Modify `backend/app/api/kline.py`: resolve local minute data after Parquet and avoid remote fallback in local mode.
- Modify `backend/app/jobs/daily_pipeline.py`: skip remote minute synchronization when the selected provider is `local_csv`.
- Modify `frontend/src/lib/api.ts`: type the minute response provider marker and preference value.
- Modify `frontend/src/components/StockIntradayChart.tsx`: show a local-data-empty message without offering a remote fetch.
- Modify `frontend/src/components/data/MinuteSyncConfig.tsx`: show local CSV mode and disable remote sync controls in that mode.
- Create `backend/tests/data_providers/test_local_minute_csv.py`: source/parser/provider tests using temporary CSV fixtures.
- Create `backend/tests/api/test_kline_minute_local.py`: API behavior tests with a fake request/repository and a guarded remote-fetch spy.

## Task 1: Lock down local CSV parsing and symbol lookup with failing tests

**Files:**
- Create: `backend/tests/data_providers/test_local_minute_csv.py`

- [ ] **Step 1: Add temporary-file fixture and failing behavior tests**

Create a UTF-8 CSV fixture under `tmp_path / "2026" / "sh600000.csv"` with the exact two-line prefix used by the supplied data:

```python
content = """数据由邢不行整理
股票代码,k线结束时间,开盘价,收盘价,最高价,最低价,成交量,成交额
sh600000,2026-01-06 09:31:00,12.10,12.20,12.30,12.00,100,1215
sh600000,2026-01-05 09:31:00,11.90,12.00,12.10,11.80,90,1075
sh600000,2026-01-06 09:30:00,12.00,12.10,12.10,12.00,80,960
"""
(tmp_path / "2026" / "sh600000.csv").write_text(content, encoding="utf-8")
```

Add tests that import `LocalMinuteCsvSource` and assert:

```python
def test_reads_chinese_minute_csv_and_filters_date(tmp_path):
    source = LocalMinuteCsvSource(tmp_path)
    rows = source.get("600000.SH", date(2026, 1, 6))

    assert rows.columns == ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
    assert rows["symbol"].to_list() == ["600000.SH", "600000.SH"]
    assert rows["datetime"].dt.strftime("%H:%M:%S").to_list() == ["09:30:00", "09:31:00"]
    assert rows["close"].to_list() == [12.1, 12.2]
    assert rows["volume"].to_list() == [80.0, 100.0]

def test_latest_date_and_exchange_mapping(tmp_path):
    write_csv(tmp_path, "sz000001", "2026-02-01 09:30:00")
    write_csv(tmp_path, "bj920000", "2026-03-01 09:30:00")

    source = LocalMinuteCsvSource(tmp_path)

    assert source.latest_date("000001.SZ") == date(2026, 2, 1)
    assert source.latest_date("920000.BJ") == date(2026, 3, 1)
    assert source.path_for_symbol("600000.SH").name == "sh600000.csv"

def test_invalid_symbol_missing_file_and_missing_date_return_empty(tmp_path):
    source = LocalMinuteCsvSource(tmp_path)

    assert source.get("600000.US", date(2026, 1, 1)).is_empty()
    assert source.get("600000.SH", date(2026, 1, 1)).is_empty()
    assert source.latest_date("600000.SH") is None
```

Use a helper `write_csv` inside the test file for the three exchange cases; keep test data local to the test and never read `D:\quant\data`.

- [ ] **Step 2: Run the focused test and verify the expected RED failure**

Run from `backend`:

```powershell
uv run pytest tests/data_providers/test_local_minute_csv.py -q
```

Expected result: collection fails with an import error because `app.data_providers.local_minute_csv` does not exist yet. If the test fails for a CSV typo instead, fix the fixture before implementing production code.

## Task 2: Implement the read-only local minute source

**Files:**
- Create: `backend/app/data_providers/local_minute_csv.py`
- Test: `backend/tests/data_providers/test_local_minute_csv.py`

- [ ] **Step 1: Implement symbol mapping and candidate file discovery**

Define `EXCHANGE_PREFIXES = {"SH": "sh", "SZ": "sz", "BJ": "bj"}` and `MINUTE_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]`. The source class must expose these concrete methods: `__init__(root: Path)`, `path_for_symbol(symbol: str) -> Path | None`, `get(symbol: str, trade_date: date) -> pl.DataFrame`, and `latest_date(symbol: str) -> date | None`.

Normalize the input symbol to uppercase, require exactly one `.` separator, require a numeric code, and return `None`/an empty result for unsupported exchanges. Search both `root / filename` and one-level year directories (`root / "*" / filename`) so the supplied layout and a flat test/deployment layout work. Do not recursively read every CSV.

- [ ] **Step 2: Implement robust CSV normalization**

Read with Polars using `skip_rows=1`, `encoding="utf8-lossy"`, and the second line as the header. Rename the Chinese source columns to canonical names, parse `k线结束时间` with `pl.col("k线结束时间").str.strptime(pl.Datetime("us"), strict=False)`, cast OHLCV columns to `Float64`, drop null datetimes, add the canonical system symbol, select `MINUTE_COLUMNS`, sort by `datetime`, and return an empty canonical frame when required columns are absent.

For files spanning multiple years, concatenate all candidate files before filtering. `get` filters with `pl.col("datetime").dt.date() == trade_date`. `latest_date` computes the maximum valid date across candidate files. Catch `FileNotFoundError`, `pl.exceptions.ComputeError`, and malformed-file exceptions per file, log a warning, and continue to any other year file.

- [ ] **Step 3: Add the provider wrapper and run the tests GREEN**

Define `LocalMinuteCsvProvider` with `name = "local_csv"`, `ProviderCapabilities(minute=True)`, and a `get_minute` method that accepts the existing provider protocol (`symbols`, `start_time`, `end_time`, `asset_type`, `freq`) and returns canonical rows by filtering each symbol's source data. Construct the source from `settings.minute_csv_dir` when no root is injected.

Run:

```powershell
uv run pytest tests/data_providers/test_local_minute_csv.py -q
```

Expected result: all source and provider tests pass.

## Task 3: Add configuration and provider registration

**Files:**
- Modify: `backend/app/config.py:68-118`
- Modify: `backend/app/services/preferences.py:97-116`
- Modify: `backend/app/data_providers/registry.py:1-15`
- Test: `backend/tests/data_providers/test_local_minute_csv.py`

- [ ] **Step 1: Add the configurable external directory**

Add `minute_csv_dir: Path = Path("D:/quant/data")` to `Settings`. Pydantic Settings will map `MINUTE_CSV_DIR` automatically; retain the existing absolute-path resolution behavior only for relative values. Do not reuse `settings.data_dir`, because that directory stores application state and Parquet files.

- [ ] **Step 2: Make local CSV the minute provider default**

Change `_ALLOWED_DATA_PROVIDERS` to `{ "tickflow", "local_csv" }`. Keep daily and adjustment defaults as `tickflow`, but change `get_minute_data_provider()` default and invalid-value fallback to `"local_csv"`. This makes the requested local source the default without changing daily, realtime, or financial data behavior.

- [ ] **Step 3: Register the provider and verify configuration selection**

Register `"local_csv": LocalMinuteCsvProvider` in `_PROVIDERS`. Add a test that monkeypatches `app.config.settings.minute_csv_dir`, calls `get_provider("local_csv")`, and confirms its `name` and `capabilities.minute` values. Also assert an invalid persisted minute provider resolves to `local_csv`.

Run:

```powershell
uv run pytest tests/data_providers/test_local_minute_csv.py -q
```

## Task 4: Route `/api/kline/minute` through local data without remote fallback

**Files:**
- Modify: `backend/app/api/kline.py:331-391`
- Create: `backend/tests/api/test_kline_minute_local.py`

- [ ] **Step 1: Write failing API behavior tests**

Build a lightweight fake request whose `app.state` contains a fake repository and a `LocalMinuteCsvSource`. The fake repository must expose `latest_minute_date`, `get_minute`, and `store`; make `get_minute` return an empty Polars frame. Monkeypatch `app.services.kline_sync.fetch_minute_single` to raise `AssertionError` if called.

Add these tests:

```python
def test_minute_endpoint_uses_local_csv_when_parquet_is_empty(tmp_path):
    response = get_minute(request, symbol="600000.SH", trade_date=date(2026, 1, 6))
    assert response["source"] == "local_csv"
    assert len(response["rows"]) == 2

def test_minute_endpoint_uses_latest_local_date_when_date_omitted(tmp_path):
    response = get_minute(request, symbol="600000.SH", trade_date=None)
    assert response["date"] == "2026-01-06"

def test_local_empty_result_does_not_call_remote_fetch(tmp_path):
    response = get_minute(request, symbol="600000.SH", trade_date=date(2026, 1, 7))
    assert response["source"] == "none"
    assert response["rows"] == []
```

- [ ] **Step 2: Run the API tests and verify RED**

Run:

```powershell
uv run pytest tests/api/test_kline_minute_local.py -q
```

Expected result: the tests fail because the route still only knows the repository/remote path and does not have a local source state or local fallback.

- [ ] **Step 3: Add local source state and helper resolution**

In `backend/app/main.py`, initialize `app.state.local_minute_source = LocalMinuteCsvSource(settings.minute_csv_dir)` after the repository is created. In `backend/app/api/kline.py`, resolve that state with a settings-based fallback for unit tests.

When no date is supplied, retain the existing Parquet latest-date lookup first; if it returns `None`, call `local_source.latest_date(symbol)`. For a selected date, keep the existing complete-Parquet fast path. When Parquet is empty or incomplete, call `local_source.get(symbol, trade_date)` and return `source="local_csv"` for non-empty rows or `source="none"` for empty rows. Remove the remote `fetch_minute_single` call from this stock endpoint.

- [ ] **Step 4: Add the provider marker and run the API tests GREEN**

Include `provider: "local_csv"` in both non-empty and empty local responses so the frontend can distinguish “no local data” from a remote-fetchable empty result. Keep existing `source="local"` for internal Parquet and `source="none"` for no rows. Run the focused API tests and the source tests; expect all to pass.

## Task 5: Prevent background and manual minute sync from reaching TickFlow in local mode

**Files:**
- Modify: `backend/app/jobs/daily_pipeline.py:406-433`
- Modify: `backend/app/api/kline.py:428-490`
- Modify: `backend/app/api/kline.py:642-808`
- Test: `backend/tests/api/test_kline_minute_local.py`

- [ ] **Step 1: Add failing guards for local mode**

Add tests that monkeypatch `preferences.get_minute_data_provider` to return `"local_csv"`, monkeypatch `kline_sync.sync_and_persist_minute` and `sync_minute_batch` to raise if called, and exercise the relevant helper/route branches. Assert local mode reports that the external CSV is already the source and does not create a remote-sync job.

- [ ] **Step 2: Guard the daily pipeline minute stage**

Read `minute_provider = preferences.get_minute_data_provider()` before the current capability branch. If it is `"local_csv"`, append `sync_minute` to the existing skipped list and log that local CSV is read-through data; do not call `sync_and_persist_minute`. Preserve the current capability-gated TickFlow branch for explicit `"tickflow"` selection.

- [ ] **Step 3: Guard manual sync and history-extension endpoints**

Before checking `Cap.KLINE_MINUTE_BATCH`, return HTTP 409 with a clear local-source message when the selected minute provider is `"local_csv"`. This applies to `/api/kline/sync_minute` and `/api/kline/extend_minute_history`; it prevents UI or direct callers from triggering remote calls in local mode.

- [ ] **Step 4: Run focused regression tests**

Run:

```powershell
uv run pytest tests/api/test_kline_minute_local.py tests/data_providers/test_local_minute_csv.py -q
```

Expected result: all local read-through and no-remote-call tests pass.

## Task 6: Make the frontend reflect local-only minute data

**Files:**
- Modify: `frontend/src/lib/api.ts:165-173,668-677`
- Modify: `frontend/src/components/StockIntradayChart.tsx:28-105`
- Modify: `frontend/src/components/data/MinuteSyncConfig.tsx:20-99,102-219`

- [ ] **Step 1: Add response and preference types**

Extend the `api.klineMinute` response type with `source?: "local" | "local_csv" | "live" | "none"` and `provider?: "local_csv" | "tickflow"`. Extend `Preferences.minute_data_provider` to include both values.

- [ ] **Step 2: Stop the minute chart from offering remote fetches in local mode**

In `StockIntradayChart`, derive `localCsvMode = minute.data?.provider === "local_csv" || minute.data?.source === "local_csv"`. When `minuteRows` is empty and `localCsvMode` is true, render a static message such as `本地 CSV 暂无该交易日分钟数据` and do not render the mutation buttons. Preserve the current fetch/retry UI for explicit TickFlow mode.

- [ ] **Step 3: Update the data settings card**

In `MinuteSyncConfig`, derive `localCsvMode` from preferences. Show a `本地 CSV` status and the configured read-through behavior. Disable the automatic sync toggle, day stepper, and history-extension controls while local mode is active; retain current Pro+ controls for TickFlow mode. Do not add an upload or conversion workflow.

- [ ] **Step 4: Run frontend type checking/build**

From `frontend` run the repository's existing package-manager command:

```powershell
pnpm build
```

Expected result: TypeScript compilation and Vite build exit with code 0.

## Task 7: Full verification and handoff

**Files:**
- No additional production files; inspect all files changed above.

- [ ] **Step 1: Run backend tests and lint**

From `backend` run:

```powershell
uv run pytest -q
uv run ruff check app tests
```

Expected result: pytest exits 0 and ruff reports no errors. Existing unrelated tests or pre-existing failures must be recorded separately rather than hidden.

- [ ] **Step 2: Verify the real local fixture read-only**

Run a one-shot Python check against `D:\quant\data` that instantiates `LocalMinuteCsvSource`, reads `600000.SH` for a date present in `sh600000.csv`, prints row count and columns, and does not write files. Confirm the source file modification time and contents are unchanged.

- [ ] **Step 3: Inspect the final diff and workspace safety**

Run:

```powershell
git diff --check
git status --short
git diff -- backend/app/config.py backend/app/data_providers/local_minute_csv.py backend/app/api/kline.py frontend/src/components/StockIntradayChart.tsx
```

Ensure only intended files from this feature are staged/committed; preserve the pre-existing dirty-worktree files listed before implementation.

- [ ] **Step 4: Commit the implementation**

Stage only the local-minute feature files and tests, then commit with:

```powershell
git add backend/app/config.py backend/app/data_providers/local_minute_csv.py backend/app/data_providers/registry.py backend/app/services/preferences.py backend/app/api/kline.py backend/app/main.py backend/app/jobs/daily_pipeline.py backend/tests/data_providers/test_local_minute_csv.py backend/tests/api/test_kline_minute_local.py frontend/src/lib/api.ts frontend/src/components/StockIntradayChart.tsx frontend/src/components/data/MinuteSyncConfig.tsx
git commit -m "feat: read minute kline data from local csv"
```
