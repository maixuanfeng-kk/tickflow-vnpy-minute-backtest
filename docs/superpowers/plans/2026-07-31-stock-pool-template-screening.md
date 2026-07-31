# Stock Pool Template Screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Screen only the selected May or June manual Watchlist template with the two requested rules, then save an immutable Stock Pool research snapshot.

**Architecture:** `StockPoolService` owns source-template validation and read-only member lookup. `StockPoolDataAdapter` accepts a symbol set and loads only those records from the normalized daily and financial datasets. The API forwards `source_pool_key`, while the React page renders a fixed source-template selector and never publishes a screen back into Watchlist.

**Tech Stack:** FastAPI, Pydantic, Polars, pytest, React, TypeScript, TanStack Query, Node test runner.

---

### Task 1: Define failing backend behavior tests

**Files:**
- Modify: `backend/tests/test_stock_pools.py`

- [ ] **Step 1: Write the failing strategy tests**

Add tests that construct qualifying ST and recently listed symbols and assert they remain eligible, because neither is a requested exclusion:

```python
def test_strategy_does_not_add_st_or_listing_age_exclusions() -> None:
    source = _strategy_input()
    daily = source.daily.with_columns(
        pl.when(pl.col("symbol") == "000001.SZ")
        .then(pl.lit("*ST测试"))
        .otherwise(pl.col("name"))
        .alias("name")
    )
    instruments = source.instruments.with_columns(
        pl.when(pl.col("symbol") == "000001.SZ")
        .then(pl.lit(date(2026, 4, 1)))
        .otherwise(pl.col("listing_date"))
        .alias("listing_date")
    )
    members = MonthlyGrowthTrendStrategy({}).build(
        StockPoolInput(source.month, source.as_of_date, daily, source.financials, instruments)
    )
    assert "000001.SZ" in members["symbol"].to_list()
```

- [ ] **Step 2: Write the failing service tests**

Create manual `month:2026-05` and `month:2026-06` pools in `tmp_path`; inject a ready adapter containing one in-template and one out-of-template qualifying symbol. Assert `build(..., "month:2026-05")` returns only the in-template symbol. Save a copy of the manual manifest and members before `save`, then assert both are byte-for-byte equivalent after saving.

- [ ] **Step 3: Run the focused test module to verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_stock_pools.py -q
```

Expected: the source-template service test fails because `build` has no source-pool argument, and the preservation test fails because `save` calls `WatchlistPoolStore.replace`.

### Task 2: Implement source-template screening and snapshot-only saves

**Files:**
- Modify: `backend/app/stock_pools/data.py`
- Modify: `backend/app/stock_pools/service.py`
- Modify: `backend/app/stock_pools/strategies.py`
- Modify: `backend/app/stock_pools/registry.py`

- [ ] **Step 1: Add source-template metadata to build results**

Extend `StockPoolBuildResult` with `source_pool_key` and `source_member_count`, so `to_dict()` includes both fields. Add a module constant in `service.py`:

```python
SUPPORTED_SOURCE_POOL_KEYS = frozenset({"month:2026-05", "month:2026-06"})
```

Resolve the selected pool through `WatchlistPoolStore.get_pool()`, reject unsupported or non-manual pools with `ValueError`, and derive the normalized symbol set from `list_members()`.

- [ ] **Step 2: Restrict adapter loading to the template symbol set**

Change the adapter load signature to accept `symbols: set[str]`. Filter both `daily` and normalized `financials` with `pl.col("symbol").is_in(symbols)`. Leave readiness dataset checks unchanged except for removing listing-age requirements and correcting the missing dataset label to `kline_daily_xbx`.

- [ ] **Step 3: Remove unrequested strategy filters**

Delete the ST-name and listing-date calculations from `MonthlyGrowthTrendStrategy`. Keep the two strict user conditions, the historical `total_mv` threshold, published-financial cutoff, MA60 five-day comparison, and strict 200-session high definition. Remove `listing_days_min` from the registry parameters and result evidence fields.

- [ ] **Step 4: Save only a research snapshot**

Update `save` to call `build` with `source_pool_key`, write `source_pool_key` and `source_member_count` into the research manifest, and return the saved run metadata. Delete the `watchlist_store.replace()` call and the `published_pool` response field.

- [ ] **Step 5: Run focused backend tests to verify GREEN**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_stock_pools.py backend\tests\test_watchlist_pools.py -q
```

Expected: all focused tests pass and manual Watchlist tests retain their original behavior.

### Task 3: Expose and validate the source pool in the API

**Files:**
- Modify: `backend/app/api/stock_pools.py`
- Modify: `backend/tests/test_stock_pools.py`

- [ ] **Step 1: Write failing API-boundary tests**

Add direct request-model validation tests for `source_pool_key="month:2026-05"` and a rejected value such as `month:2026-07`. The route must return HTTP 400 for unsupported keys without writing a research run.

- [ ] **Step 2: Run the tests to verify RED**

Run the focused backend test module and confirm the new test fails because the request model does not contain `source_pool_key`.

- [ ] **Step 3: Add the request field and forward it**

Add `source_pool_key: str` to `StockPoolBuildRequest`; pass it to readiness, preview, and save service calls. Catch the service validation error as the existing HTTP 400 path does.

- [ ] **Step 4: Run backend tests to verify GREEN**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_stock_pools.py -q
```

Expected: all tests pass.

### Task 4: Bind the fixed source-template choices in the frontend

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/queryKeys.ts`
- Modify: `frontend/src/pages/StockPools.tsx`
- Create: `frontend/tests/stock-pool-template-screening.test.mjs`

- [ ] **Step 1: Write the failing frontend structural test**

Create a Node test that reads `StockPools.tsx` and asserts it contains both `month:2026-05` and `month:2026-06`, does not contain `type="month"`, contains `保存筛选快照`, and does not contain `保存并发布` or `invalidateQueries({ queryKey: QK.watchlistPools })`.

- [ ] **Step 2: Run the test to verify RED**

Run:

```powershell
node --test frontend/tests/stock-pool-template-screening.test.mjs
```

Expected: failure because the page still renders a free month input and publish workflow.

- [ ] **Step 3: Update API types and request helpers**

Add `source_pool_key` and `source_member_count` to Stock Pool response types. Change readiness, preview, and save helpers to receive `sourcePoolKey`; derive the month in the API payload from the selected key only where the existing route contract still requires it.

- [ ] **Step 4: Replace the page workflow**

Use a fixed select with May and June options, display its read-only member count from the Watchlist-pool query, and call all Stock Pool APIs with its `sourcePoolKey`. Rename the save button and success toast to snapshot terminology. Remove the overwrite confirmation and all Watchlist query invalidations from successful saves. Show the source template for saved runs.

- [ ] **Step 5: Run frontend checks to verify GREEN**

Run:

```powershell
node --test frontend/tests/stock-pool-template-screening.test.mjs
pnpm --dir frontend build
```

Expected: structural test and TypeScript/Vite build pass.

### Task 5: End-to-end verification and delivery

**Files:**
- Create: `docs/deliveries/2026-07-31-stock-pool-template-screening.md`

- [ ] **Step 1: Run all affected checks**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_stock_pools.py backend\tests\test_watchlist_pools.py backend\tests\test_watchlist_pool_api.py -q
node --test frontend/tests/stock-pool-template-screening.test.mjs
pnpm --dir frontend build
git diff vnpy-origin/main...HEAD --check
```

- [ ] **Step 2: Write the delivery record**

Record the branch, commits, files, preserved manual-template behavior, changed snapshot behavior, exact verification results, limitations (the user must import professional daily and financial datasets), rollback via reverting the merge commit, and that no merge conflicts occurred.

- [ ] **Step 3: Commit and push the feature branch**

Explicitly stage only the changed Stock Pool files, tests, plan, and delivery record. Inspect the staged diff, commit with a descriptive message, and push to `vnpy-origin` without force.

- [ ] **Step 4: Merge and verify main**

After `main` is clean and synchronized with `vnpy-origin/main`, merge this feature branch using `git merge --no-ff`, rerun the affected checks on `main`, push `vnpy-origin main`, and verify `main...vnpy-origin/main` reports `0 0`.
