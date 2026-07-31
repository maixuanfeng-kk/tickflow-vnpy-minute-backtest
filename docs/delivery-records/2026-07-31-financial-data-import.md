# Delivery record: local financial data import

## Objective

Add a Data-page workflow that imports a user-selected, already-extracted XBX financial CSV directory in the background. The import publishes raw financial fields and the existing point-in-time income projection for future stock-pool strategies; it does not add or change stock-pool selection rules.

## Branch and scope

- Feature branch: `feature/20260731-financial-data-import`
- Target repository and branch: `vnpy-origin/main`
- Changed areas: financial-import API, Data-page import panel, API query types, tests, implementation plan, and this delivery record.

## Changes

- Register `POST /api/data/financial-import` and `GET /api/data/financial-import/status`.
- Validate the manually entered directory, require at least one CSV, and reject starts while another global data task is active.
- Reuse the existing XBX importer, job store, and heavy-task lock; publish task progress and the latest import summary without exposing the local source path.
- Release the heavy-task lock only after import and cache refresh work completes, then write the success or failure terminal job state.
- Add a standalone Data-page panel with directory input, progress, import summary, and up to three failed-file reasons.
- Keep financial import out of the generic daily-K pipeline card so it does not render daily-K metrics as zero.

## Preserved behavior

- Existing daily-K pipeline, cache refresh, stock-pool strategies, and financial data reader behavior remain unchanged.
- Existing user financial data stays published when a new import cannot produce valid records.
- The browser does not persist the local source directory.

## Verification

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
& D:\quant\tickflow-stock-panel\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_financial_import.py backend/tests/test_stock_pools.py -q
# 16 passed; existing datetime.utcnow deprecation warnings only

& D:\quant\tickflow-stock-panel\backend\.venv\Scripts\python.exe -m ruff check backend/app/api/financial_import.py backend/tests/test_financial_import.py
# All checks passed

cd frontend
pnpm exec tsc -b
pnpm exec vite build
# passed; Vite reports existing large-chunk advisory warnings
```

## Compatibility, risks, and rollback

- Input remains a local, pre-extracted directory rather than ZIP upload; callers supply it for each import.
- A cache-refresh exception marks the import job failed even if the importer has already published financial files; the UI preserves the prior/latest published dataset and exposes the task error.
- No data migration, secret, token, archive, cache, or build artifact is committed.
- Roll back the merged feature with `git revert -m 1 <main-merge-sha>`; this reverts application code without deleting locally imported financial data.

## Conflict record

No merge conflicts occurred. No entire business file was selected from either branch.
