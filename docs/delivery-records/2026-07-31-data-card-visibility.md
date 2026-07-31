# Delivery record: local data overview cards

## Objective

Restore the minute-K card in the Data-page overview and render the local financial-import dataset as a matching overview card.

## Branch and scope

- Feature branch: `feature/data-card-visibility`
- Target repository and branch: `vnpy-origin/main`
- Changed areas: Data-page card visibility migration, local-data card labeling, financial-import status mapping, frontend regression test, implementation plan, and this delivery record.

## Changes

- Make minute K and financial data visible by default in Data-page settings, independent of paid API capability checks.
- Migrate legacy saved card settings once so the prior default-hidden state no longer suppresses locally available data; later manual visibility choices remain unchanged.
- Make “恢复默认” retain the normal default visibility policy instead of only showing the first five cards.
- Label the local minute-K cache and local financial-import dataset as local data.
- Fill the financial card from the existing import-status endpoint with report row count, covered symbols, newest report date, and newest publish date.

## Preserved behavior

- The minute-K import/storage format and existing 243 date partitions are unchanged; the UI does not aggregate all minute rows.
- The financial import panel remains the only action surface for selecting a local source directory and starting an import.
- Users can still reorder or manually hide either card from page settings after migration.

## Verification

```powershell
cd frontend
node --experimental-strip-types --test tests/watchlist-text-import.test.mjs tests/opening-volume-execution.test.mjs tests/data-card-visibility.test.mjs
# 6 passed

pnpm exec tsc -b
# passed

pnpm exec vite build
# passed; only the existing Vite large-chunk advisory appeared
```

Manual UI check at `http://127.0.0.1:3020/data` confirmed that the minute-K card renders 243 trading days with its local date range and that the financial-data card renders in the same overview grid.

## Compatibility, risks, and rollback

- The one-time visibility migration intentionally turns on the two local-data cards for legacy settings. Subsequent user choices are persisted under version 2 and are not altered.
- An unimported financial dataset renders an empty overview card until the existing import workflow completes.
- Roll back with `git revert -m 1 <main-merge-sha>`; no local minute-K or imported financial data is deleted.

## Conflict record

No merge conflicts occurred. No existing data or import behavior was removed.
