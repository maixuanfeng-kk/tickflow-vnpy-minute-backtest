# Full-Market Stock Pool Delivery Record

## Delivery Record

- Task/goal: Correct Stock Pool screening so the two configured conditions run
  against the full imported market universe. Save each monthly result as an
  independently selectable generated Watchlist pool for viewing and backtests.
- Feature branch: `feature/stock-pool-full-universe`.
- Feature commit: `c761fed` (`feat(stock-pools): generate full-market monthly pools`).
- Merge commit: `5fa953d` (`merge(stock-pools): add full-market monthly generated pools`).
- Changed files and purpose:
  - `backend/app/stock_pools/data.py`: loads the full daily and financial
    universe when no symbol subset is supplied.
  - `backend/app/stock_pools/service.py` and `backend/app/api/stock_pools.py`:
    make the previous source-template input optional, leave it out of the
    screening universe, and publish each saved result as a generated pool.
  - `backend/app/services/watchlist_pools.py`: stores generated pools under
    stable `generated:<strategy>:<month>` keys alongside manual month pools.
  - `frontend/src/pages/StockPools.tsx`: provides a month picker, identifies
    the universe as full market, and refreshes Watchlist pools after save.
  - `frontend/src/pages/Watchlist.tsx` and
    `frontend/src/pages/backtest/StrategyBacktest.tsx`: display generated-pool
    labels so same-month manual and generated pools are separately selectable.
  - Backend and frontend tests: prove template-external qualified symbols are
    selected and a generated pool is readable without changing manual members.
- Preserved behavior: Existing manual Watchlist pools and their member files
  are not overwritten. Their existing views and backtest selection continue to
  use their original `month:YYYY-MM` keys.
- New behavior: Saving `monthly_growth_trend` for `2026-05` creates or updates
  `generated:monthly_growth_trend:2026-05`, labeled `2026-05 条件选股结果`.
  The same result pool can be selected from Watchlist or the backtest month-pool
  selector. The old optional source-template API field no longer limits the
  candidate universe.
- Verification performed on merged `main`:
  - `python -m pytest backend/tests/test_stock_pools.py backend/tests/test_watchlist_pools.py backend/tests/test_watchlist_pool_api.py -q`: 22 passed.
  - `python -m ruff check --select F backend/app/api/stock_pools.py backend/app/stock_pools/data.py backend/app/stock_pools/service.py backend/app/services/watchlist_pools.py backend/tests/test_stock_pools.py`: passed.
  - `node --test tests/stock-pool-template-screening.test.mjs`: 2 passed.
  - `vite build`: passed.
  - `git diff --check` and conflict-marker scan: passed before merge.
- Known limitation: Actual construction still requires professional daily data
  in `DATA_DIR/kline_daily_xbx` and normalized financial income data in
  `DATA_DIR/financials/income/part.parquet`.
- Rollback: `git revert -m 1 5fa953d`.
- Conflict record: None.
