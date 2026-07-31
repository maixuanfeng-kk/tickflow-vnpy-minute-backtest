# Stock Pool Template Screening Delivery Record

## Delivery Record

- Task/goal: Limit Stock Pool screening to the existing manual `2026-05` and
  `2026-06` Watchlist templates, use historical market capitalization and
  disclosed financial data, and save an independent screening snapshot.
- Branch: `feature/20260731-stock-pool-template-screening`.
- Changed files and purpose:
  - `backend/app/stock_pools/data.py`: restricts loaded daily and financial
    records to the source-template symbols and identifies the professional
    daily source directory.
  - `backend/app/stock_pools/service.py` and `backend/app/api/stock_pools.py`:
    validate source templates, carry template metadata, and save snapshots
    without writing Watchlist pools.
  - `backend/app/stock_pools/strategies.py` and `registry.py`: retain exactly
    the requested two conditions and remove ST/listing-age exclusions.
  - `frontend/src/lib/api.ts`, `frontend/src/lib/queryKeys.ts`, and
    `frontend/src/pages/StockPools.tsx`: provide the fixed May/June selector
    and snapshot-only save workflow.
  - Backend and frontend tests: cover source membership, template preservation,
    API request data, and frontend workflow structure.
- Preserved behavior: Manual monthly Watchlist pools, including their manifests
  and members, remain available for viewing and backtesting and are never
  overwritten by Stock Pool saves.
- New behavior: Stock Pool preview and save require `month:2026-05` or
  `month:2026-06`; all returned symbols are from the selected manual template;
  saved results carry source-template metadata as research snapshots.
- Verification performed:
  - `python -m pytest backend/tests/test_stock_pools.py backend/tests/test_watchlist_pools.py backend/tests/test_watchlist_pool_api.py -q`: 22 passed.
  - `python -m ruff check --select F app/stock_pools app/api/stock_pools.py tests/test_stock_pools.py`: passed.
  - `node --test frontend/tests/stock-pool-template-screening.test.mjs`: 2 passed.
  - `pnpm 9.10.0 --dir frontend build`: passed.
  - `git diff --check` and conflict-marker scan: passed.
- Known limitation: Actual screening remains unavailable until both professional
  daily data and normalized financial data are imported into the configured
  `DATA_DIR`. Professional daily CSV source: `F:\quant\data\value\stock-trading-data-pro`.
- Rollback: Revert the eventual merge commit with `git revert -m 1 <merge-sha>`.
- Conflict record: None.
