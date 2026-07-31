# Stock Pool Template Screening Design

## Goal

Allow Stock Pools to screen only the members of the existing `2026-05` and
`2026-06` manual Watchlist templates. The source templates remain unchanged.

## Scope

- Supported source templates are initially `month:2026-05` and
  `month:2026-06`.
- The strategy returns the union of these two user-specified conditions:
  1. total market capitalization is greater than CNY 10 billion, revenue YoY
     is greater than 15 percent, and each of the latest five closes is above
     its 60-session moving average.
  2. total market capitalization is greater than CNY 10 billion, net profit
     is greater than zero, and a strict 200-session high occurred within the
     most recent 20 sessions.
- The strategy uses no additional ST or listing-age exclusion because those
  filters were not requested.

## Data Flow

`F:\quant\data\value\stock-trading-data-pro` is the user-managed source
directory for professional daily CSV files. The existing XBX importer reads
that directory and writes normalized historical daily data, including
`total_mv` in CNY, to `<DATA_DIR>\kline_daily_xbx`. It does not move, rename,
or modify the source CSV files.

The financial importer continues to write normalized income statements to
`<DATA_DIR>\financials\income\part.parquet`. Both imported datasets are
required before a preview is ready. The backend uses only financial reports
published on or before the monthly as-of date.

## Backend Contract

Preview and save requests gain a `source_pool_key` field. The backend accepts
only `month:2026-05` and `month:2026-06`, verifies that the pool exists and is
manual, and reads its members through `WatchlistPoolStore.list_members()`.
The data adapter limits daily and financial data to these symbols before the
strategy is evaluated.

The result includes the selected source pool key and its member count. Saving
creates a StockPool research snapshot with the source-template metadata. It
never calls `WatchlistPoolStore.replace()` and therefore cannot change the
manual template's manifest or members.

## Frontend

The Stock Pools page replaces the free month input with the two source-template
options. It displays the template month and member count as read-only context.
The save action is named `Save screening snapshot`; it does not present an
overwrite confirmation or invalidate Watchlist queries. Existing saved runs
remain listable and their source template is visible.

## Errors and Readiness

The readiness endpoint validates the selected source template before assessing
daily and financial data. Its missing-data response identifies the normalized
dataset expected by the screen and identifies the professional daily import
source as `F:\quant\data\value\stock-trading-data-pro`.

## Verification

- A service test proves that a successful screen cannot return a symbol outside
  its source template.
- A save test proves that a manual template's members and manifest are unchanged.
- Strategy tests cover the strict market-cap boundary, disclosed financial
  cutoff, five-day MA condition, and strict 200-day-high condition.
- API tests reject unsupported source pool keys.
- Frontend structural tests verify the fixed source-template selector and the
  snapshot-only save behavior.
