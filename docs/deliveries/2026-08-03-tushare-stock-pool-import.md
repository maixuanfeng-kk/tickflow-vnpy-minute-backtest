# Tushare Stock Pool Import Delivery Record

## Delivery Record

- Task/goal: Allow the Data page cards to import local Tushare daily K-line and
  financial CSV files, then make those normalized datasets available to the
  existing monthly stock-pool screen without modifying manual month templates.
- Feature branch: `feature/20260803-tushare-stock-pool`.
- Changed files and purpose:
  - `backend/app/services/local_daily_pro_import.py`: accepts the compact
    Tushare daily schema and converts `total_mv`/`circ_mv` from ten-thousand
    CNY to CNY.
  - `backend/app/services/local_financial_import.py`: accepts Tushare financial
    wide CSV files and projects announced revenue and net income into the
    existing normalized income table.
  - `backend/app/api/daily_pro_import.py`: provides a background card-import
    API for `YYYY/000001_SZ.csv` local daily directories.
  - `backend/app/stock_pools/data.py`: prefers the imported professional daily
    dataset while retaining XBX daily data as a compatibility fallback.
  - `frontend/src/components/data/DailyProImportPanel.tsx` and Data-page API
    bindings: expose the daily K card import flow with progress and summary.
  - Tests: cover compact daily files, market-cap units, Tushare financial
    projection, card API validation, and monthly pool loading.
- Preserved behavior: Existing XBX financial imports, existing XBX daily data,
  manual month pools, generated pools, Watchlist selection, and backtest pool
  selection remain unchanged.
- New behavior: The 日K card settings can import
  `F:\quant\tushare\A股日K`; the 财务数据 card accepts
  `F:\quant\tushare\财报`. Stock pools read `kline_daily_pro` when available
  and use its CNY-normalized market capitalization.
- Data compatibility: Tushare financial input requires `ts_code`, `end_date`,
  and either `inc_f_ann_date` or `inc_ann_date`; it uses `inc_revenue` with
  `inc_total_revenue` fallback and `inc_n_income` with attributable-profit
  fallback. Daily input requires the standard OHLC, volume, amount, and market
  value fields.
- Verification performed before main integration:
  - `python -m pytest tests/test_daily_pro_import_api.py tests/test_local_research_imports.py tests/test_stock_pools.py tests/test_financial_import.py`: 29 passed.
  - `node --test tests/card-import-entrypoints.test.mjs tests/stock-pool-template-screening.test.mjs`: 5 passed.
  - `git diff --check`: passed.
  - `pnpm run build`: reached TypeScript validation but is blocked by the
    pre-existing unused `watchlistPools` declaration in `src/pages/StockPools.tsx`.
- Known limitation: The first full import scans several thousand per-stock CSV
  files and can take a substantial time. A new import replaces affected
  professional-daily date partitions, but leaves the old XBX daily dataset
  intact.
- Rollback: revert the feature commit. Existing XBX data remains available as
  the fallback dataset after rollback.
- Conflict record: Pending main synchronization; remote backtest changes must
  be merged while preserving both the new daily-import API bindings and the new
  backtest API bindings.
