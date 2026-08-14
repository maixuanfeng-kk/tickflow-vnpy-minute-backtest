# ETF 159915 Differences-Only Audit Workbook Design

## Goal

Create a new Excel workbook that mirrors the readable review style of the existing
`ETF159915_逐笔交易审计_2023-2025_含日志时点复算.xlsx`, but contains only discrepancies between
the current TickFlow replay and `trade_log(1).csv` for 2023-01-01 through 2025-12-31.

## Scope

The workbook contains one worksheet named `逐笔差异复盘`. It excludes all exact matches.
The expected discrepancy population is:

- 9 same-direction, same-rule pairs with different timestamps.
- 3 same-day, same-direction pairs with different rules.
- 4 external-log-only signals.
- 7 TickFlow-only signals.

Paired discrepancies occupy one row. External-only and local-only signals also occupy one
row, leaving the unavailable side blank and explaining why no pair exists.

## Columns

Each row includes the discrepancy category, year, direction, external time/rule/price,
local signal time/fill time/rule/signal price/fill price, time and price deltas, raw minute
and daily inputs, evaluated conditions, the direct mismatch explanation, trading state,
and the relevant execution or pairing details.

## Data Sources

- External report: `trade_log(1).csv` supplied by the user.
- Local signals: a fresh zero-slippage TickFlow replay using the current strategy.
- Market data: local ETF daily and minute Parquet datasets.
- Strategy logic: `backend/app/vnpy_backtest/strategies/etf_159915_minute.py`.

## Presentation

Use the reference workbook's restrained navy, blue, pale gray, amber, and red palette.
Freeze the title/header area and identifying columns, enable filtering, wrap long review
text, use real Excel datetime and numeric values, and visually distinguish the four
discrepancy categories. The workbook must remain a single worksheet.

## Verification

- Reconcile the worksheet to exactly 23 discrepancy rows.
- Reconcile category counts to 9, 3, 4, and 7.
- Inspect the exported values and scan for formula errors.
- Render the worksheet in sections and visually check headers, widths, wrapping, and
  category highlighting before delivery.
