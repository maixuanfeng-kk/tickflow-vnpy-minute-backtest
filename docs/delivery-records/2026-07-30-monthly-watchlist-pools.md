# Delivery record: monthly watchlist pools and text import

## Objective

Integrate independently editable monthly watchlist pools, Stock Pools publication, month-scoped backtests, legacy watchlist migration, and text batch import into `vnpy-origin/main` without replacing the existing vn.py backtest workflow.

## Branches and commits

- Feature branch: `feature/20260730-monthly-watchlist-pools-delivery`
- Feature commits: `f1e216c`, `22ef4ef`, `aac2696`
- Functional integration merge: `8c39887ceefb6db7e8fa2ddad34250ed68e46986`
- Delivery-record branch: `docs/20260730-monthly-watchlist-delivery`

## Changes

- Store monthly pools under the existing user-data area, with month validation, member ordering, aggregate views, and idempotent legacy migration to `2026-01`.
- Extend watchlist APIs with pool-aware list, add, batch add, remove, move-to-top, and clear operations while preserving legacy callers.
- Publish saved Stock Pools members to their corresponding monthly watchlist pool.
- Add watchlist month tabs, an aggregate view, new-month creation, and a visible legacy migration action.
- Add full market, manual pool, and monthly-pool choices to backtests. Monthly members are re-read and frozen into the request at run time.
- Keep image recognition import and add text batch import for standard `000021.SZ`-style codes. Commas, spaces, semicolons, and line breaks are accepted; duplicates are removed and the dialog names the active destination pool.
- Restore the opening-volume pool resolver required by the combined flow: explicit symbols take precedence and an empty manual pool falls back to the current watchlist.

## Preserved behavior

- Existing screenshot-based watchlist import remains available.
- Existing vn.py opening-volume execution, engine selection, and manual code input remain available.
- Existing all-market and manual backtest scopes remain available; monthly pools add a third explicit scope.
- No local data, migration result, `.env`, tokens, logs, or build artifacts are committed.

## Verification

Executed on functional integration merge `8c39887`:

```powershell
cd backend
$env:PYTHONPATH='D:\quant\tickflow-stock-panel-main-delivery\backend'
& D:\quant\tickflow-stock-panel\backend\.venv\Scripts\python.exe -m pytest -q
# 494 passed, 1 skipped, 10 existing deprecation warnings

cd ../frontend
corepack pnpm install --frozen-lockfile
node --experimental-strip-types --test tests\opening-volume-execution.test.mjs tests\watchlist-text-import.test.mjs
# 4 passed
corepack pnpm build
# passed
```

## Conflict record

| File | Existing main behavior | Added behavior | Final behavior |
| --- | --- | --- | --- |
| `frontend/src/pages/backtest/StrategyBacktest.tsx` | vn.py opening-volume backtest configuration | Month-pool source selector and run-time member freezing | Both behaviors coexist; frozen monthly members are passed to opening-volume execution |
| `docs/superpowers/plans/2026-07-30-monthly-watchlist-pools.md` | Deleted from main | Historical implementation plan | Remains deleted because it has no run-time or external contract; this delivery record replaces the delivery metadata |

No entire business file was selected from one side. The merge initially exposed a missing opening-volume resolver dependency; a regression test now covers explicit-symbol priority and watchlist fallback.

## Compatibility, limitations, and rollback

- Existing no-pool API callers resolve through the service default path; month-aware callers pass `pool_key` explicitly.
- Text import accepts only six-digit codes with `.SH`, `.SZ`, or `.BJ` suffixes. Invalid pasted text is ignored before submission.
- To roll back the functional feature from `main`, run:

```powershell
git revert -m 1 8c39887ceefb6db7e8fa2ddad34250ed68e46986
```

## Multi-remote delivery exception

The local branch named `main` tracks a different repository (`origin`). The requested repository is `vnpy-origin`, so the integration used a temporary branch created directly from `vnpy-origin/main` and will push that verified branch to `vnpy-origin/main`. This preserves the target history, uses a non-fast-forward merge, and does not force-push or rewrite any shared commit.
