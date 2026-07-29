# Delivery record: stock pools and vn.py opening-breakout coexistence

## Objective

Integrate the local CSV-driven research stock-pool workflow and vn.py opening-breakout portfolio backtest on `feature/20260729-vnpy-breakout-coexist`, without replacing the native minute-portfolio backtest already in `main`.

## Changes

- Added local daily, financial, and minute CSV import services and administrator commands. They write standard Parquet; no local paths, source CSV, or data partitions are committed.
- Added the stock-pool module and `/api/stock-pools`: monthly preview/save snapshots and a copyable `600000.SH,...` stock-code string.
- Added the independent vn.py portfolio engine, market rules, daily minute replay, and strategy registry. `opening_breakout_pool` and `opening_breakout_condition_1` accept 1–1000 symbols.
- Added an engine selector to the existing backtest page. The vn.py branch accepts pasted stock-pool codes and supports maximum positions, equal/score-weighted sizing, 3% fixed reserve, the 10% minute-volume-limit switch, T+1, and next-minute-open matching.
- Preserved `/minute-portfolio/stream`, native minute-portfolio settings, result display, and tests. The vn.py route is namespaced under `/vnpy/*` and reads only local standard minute Parquet.

## Shared-file resolution

| File | Existing main behavior | Added behavior | Final behavior |
| --- | --- | --- | --- |
| `backend/app/api/backtest.py` | Native minute-portfolio stream | vn.py stream, registry, progress and cancellation | Separate API routes coexist |
| `frontend/src/pages/backtest/StrategyBacktest.tsx` | Native minute-portfolio UI | Engine selector and vn.py pool input | Existing UI retained; vn.py is an additive branch |
| `frontend/src/lib/backtestTask.ts` | Native SSE task | vn.py task parameters and 1–1000 validation | Stream selected by engine |
| API types, router, navigation | Existing main UI/API | Stock-pool types, requests, and page | Incremental additions only |

## Verification

```powershell
cd backend
python -m pytest tests/test_local_minute_import.py tests/test_stock_pools.py tests/test_local_research_imports.py tests/vnpy_backtest tests/backtest/test_minute_portfolio.py tests/backtest/test_minute_portfolio_api.py -q
# 133 passed, 1 warning

cd ../frontend
corepack pnpm build
# passed
```

The tests cover local imports, stock-pool readiness, vn.py registry/portfolio matching/board rules/equal and score-weighted allocation/T+1/volume limit, plus the native `minute_portfolio` regression path.

## Compatibility, limitations, and rollback

Configure local CSV directories only in server `.env`; do not commit `.env` or data files. Stock-pool snapshots remain local research output. Backtests deliberately use user-copied/pasted codes rather than automatically binding saved pools.

The two minute engines share one page but do not share an execution engine. Roll back the merge with:

```powershell
git revert -m 1 <merge-commit>
```

## Conflict record

There were no Git text conflicts. The backtest page and task files required semantic integration: no file was overwritten wholesale; the existing native path is retained and the vn.py path is selected explicitly by engine mode.
