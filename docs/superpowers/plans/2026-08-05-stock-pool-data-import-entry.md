# Stock Pool Data Import Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development while executing each task.

**Goal:** Add an actionable professional daily-K import entry to Stock Pools readiness and make `kline_daily_pro` readable by the current adapter.

**Architecture:** Reuse the existing CSV importer and `pipeline_jobs` single-flight background job model. Add a small API router for start/status, a focused React dialog launched from the readiness panel, and a data-adapter fallback that prefers the populated professional dataset.

**Tech Stack:** FastAPI, Polars, Python job store, React 18, TanStack Query, lucide-react, pnpm/Vite.

---

### Task 1: Lock the data contract with failing backend tests

**Files:**
- Modify: `backend/tests/test_stock_pools.py`
- Modify: `backend/tests/test_local_research_imports.py`
- Create: `backend/tests/test_daily_pro_import_api.py`

- [ ] Add tests for pro-dataset preference/readiness coverage, compact daily CSV market-cap normalization, and API directory validation.
- [ ] Run the focused tests and confirm they fail because the adapter still hard-codes XBX, the importer does not normalize compact input consistently, and the router does not exist.

### Task 2: Make professional daily data usable

**Files:**
- Modify: `backend/app/stock_pools/data.py`
- Modify: `backend/app/services/local_daily_pro_import.py`

- [ ] Add dataset selection that prefers populated `kline_daily_pro`, reports the selected dataset, and falls back to XBX.
- [ ] Make `load()` tolerate pro rows without `name` by supplying the symbol as the strategy input name.
- [ ] Normalize pro `total_mv`/`circ_mv` to CNY and accept both full and compact Tushare daily headers.
- [ ] Run the Task 1 backend tests and confirm green.

### Task 3: Add the import API

**Files:**
- Create: `backend/app/api/daily_pro_import.py`
- Modify: `backend/app/main.py`

- [ ] Add `POST /api/data/daily-pro-import` with annual-directory validation and a background job.
- [ ] Add `GET /api/data/daily-pro-import/status` with sanitized manifest/job data.
- [ ] Invalidate data caches and refresh repository views after success; release the run slot before marking terminal state.
- [ ] Run API and importer tests.

### Task 4: Add the focused frontend entry

**Files:**
- Create: `frontend/src/components/stock-pools/StockPoolDataImportDialog.tsx`
- Modify: `frontend/src/pages/StockPools.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/queryKeys.ts`
- Create: `frontend/tests/stock-pool-data-import-entry.test.mjs`

- [ ] Add a labeled readiness-panel button with the import icon.
- [ ] Add the dialog with a server-local directory field, start action, progress, terminal error/success summary, and close action.
- [ ] Invalidate readiness and import status queries when the job completes.
- [ ] Add the frontend contract test and run it.

### Task 5: Verify the integrated change

- [ ] Run focused backend tests: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_stock_pools.py backend/tests/test_local_research_imports.py backend/tests/test_daily_pro_import_api.py -q`.
- [ ] Run frontend contract tests: `pnpm --dir frontend exec node --test tests/stock-pool-data-import-entry.test.mjs`.
- [ ] Run production build: `pnpm --dir frontend build`.
- [ ] Query `/api/stock-pools/readiness` against the running backend and verify `coverage.daily_dataset` reports the selected source.
