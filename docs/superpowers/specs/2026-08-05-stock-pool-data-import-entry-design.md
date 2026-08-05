# Stock Pool Data Import Entry Design

## Goal

Turn the Stock Pools readiness warning into an actionable import workflow. A user who is missing professional daily K-line data can start a local CSV import from the readiness panel, follow progress, see failures, and have readiness refresh automatically when the job finishes.

## Interaction

- Show `导入所需数据` in the readiness panel only while data is not ready.
- Open a focused dialog instead of navigating away from Stock Pools.
- Keep the current readiness explanation and missing-item list visible in the dialog.
- Accept a server-local directory containing `YYYY/*.csv` professional daily files.
- Show pending/running progress, terminal success/failure, and the latest import coverage summary.
- Refresh Stock Pools readiness automatically after the import reaches a terminal state.

## Backend

- Add `POST /api/data/daily-pro-import` to validate the source directory and start one background import job.
- Add `GET /api/data/daily-pro-import/status` to expose the latest relevant job and sanitized import manifest.
- Reuse `LocalDailyProCsvImporter`, the existing pipeline job store, and the global run-slot lock.
- Preserve the previously published dataset when an import fails.

## Data Compatibility

- Stock Pools prefers `kline_daily_pro` when it contains Parquet data and falls back to `kline_daily_xbx`.
- The readiness response reports the dataset actually selected.
- Tushare-style daily data may omit a historical name; the adapter supplies a stable fallback name so the existing strategy input contract remains valid.
- Professional daily import normalizes `total_mv` and `circ_mv` from ten-thousand CNY to CNY.

## Scope

This change does not add browser file uploads, a generic import framework, or new importers for instrument and financial datasets. Those requirements remain explicit in the readiness result and use their existing data pipelines.

## Verification

- Backend tests prove the adapter selects `kline_daily_pro`, reports it, and reads it.
- API tests prove valid annual directories start a job and invalid directories are rejected.
- Importer tests prove market-cap unit normalization.
- A focused frontend contract test proves the readiness action and dialog/API wiring exist.
- Run focused backend tests, frontend contract tests, and the production frontend build.
