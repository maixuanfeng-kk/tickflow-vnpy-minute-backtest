# Delivery record: local minute and financial import panels

## Objective

Expose local-path import workflows for both minute-K CSV data and financial CSV data directly on the Data page.

## Branch and scope

- Feature branch: `feature/local-data-import-panels`
- Target repository and branch: `vnpy-origin/main`
- Changed areas: local minute-import API, route registration, API client types, Data-page import layout, minute import panel, regression test, and this delivery record.

## Changes

- Add `POST /api/data/minute-import` and `GET /api/data/minute-import/status`.
- Reuse the existing local minute CSV importer, standard minute-K store, job progress store, cache refresh, and global heavy-task lock.
- Validate the user-provided directory and scan only the CSV files that the existing minute importer consumes.
- Do not return the local source directory from the status endpoint.
- Add a minute-K path import panel with import state, progress, file/row summary, date range, and failed-file details.
- Group the new minute-K panel and the existing financial-path import panel beneath the `本地数据导入` section of the Data page.

## Preserved behavior

- Existing minute-K API synchronization settings and all locally imported minute data remain unchanged.
- Existing financial import validation, progress, and stock-pool data availability remain unchanged.
- Each import still obtains the shared heavy-task lock, so two data imports cannot write the local store concurrently.

## Verification

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
& D:\quant\tickflow-stock-panel\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_minute_import_api.py backend/tests/test_local_minute_import.py backend/tests/test_financial_import.py -q
# 12 passed; existing datetime.utcnow deprecation warnings only

& D:\quant\tickflow-stock-panel\backend\.venv\Scripts\ruff.exe check backend/app/api/minute_import.py backend/tests/test_minute_import_api.py
# All checks passed

cd frontend
pnpm exec tsc -b
pnpm exec vite build
# passed; existing Vite large-chunk advisory only
```

Manual Data-page validation confirmed both local-path input panels and their `扫描并导入` buttons render under `本地数据导入`.

## Compatibility, risks, and rollback

- Minute CSV source format remains the existing GBK `sh|sz|bj` filename convention; the panel does not change normalization or overwrite rules.
- The frontend does not persist either local source path.
- Roll back with `git revert -m 1 <main-merge-sha>`; this reverts UI/API code without deleting locally imported minute or financial data.

## Conflict record

No merge conflicts occurred. No existing import workflow was removed.
