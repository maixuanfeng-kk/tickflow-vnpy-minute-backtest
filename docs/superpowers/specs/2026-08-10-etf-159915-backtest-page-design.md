# 159915 ETF Backtest Extension Design

## Goal

Keep the existing `/backtest` workspace and all existing strategy behavior, while adding three strategy-specific capabilities when `strategy_id` is `etf_159915_minute`:

1. A compact rule and fixed-setting summary.
2. A pre-run daily/minute data readiness gate.
3. A signal-to-next-minute-fill execution trace.

## Compatibility Boundary

- The route, two-column layout, generic strategy selector, generic parameters, task lifecycle, result overview, and existing result tabs remain unchanged.
- Strategy-specific UI is mounted through a small extension boundary keyed by `strategy_id`.
- Other strategies do not request ETF readiness data and do not render ETF modules or the execution tab.
- Existing result payload fields remain valid. The frontend only exposes the already-returned `fills` collection in its TypeScript type.

## Page Layout

Use the approved split placement:

- Left configuration column, before the run action:
  - `Etf159915RuleSummary`
  - `Etf159915DataReadiness`
- Right result column:
  - Add `执行追踪` beside the existing result tabs only for a 159915 result.
  - Render `Etf159915ExecutionTrace` inside that tab.

Switching away from 159915 unmounts the extension and restores the existing page.

## Rule Summary

The collapsed summary shows the immutable strategy contract:

- Symbol: `159915.SZ`
- Raw, unadjusted one-minute bars
- Signal on completed minute close
- Fill at the next real minute open
- 97% cash deployment, single position, T+1
- Rule version: `2026-08-07`

The expanded view groups the rule families instead of reproducing the full document:

- Entries: `4.1.1`, `4.1.2`, `4.2.1`, `4.2.2`, `4.2.3-1`, `4.2.3-2`, `tail_1..3`
- Exits: `5.1.1..3`, `5.2`, `5.3`, `5.4`, `5.4_rebuy`, `5.5`, `5.6`
- Priority constraints: `4.2.3` exclusivity and `5.4 > 5.3 > 5.2 > regular exits`

This section is read-only. It explains the code contract but does not create a second editable source of strategy logic.

## Data Readiness API

Add:

`GET /api/backtest/vnpy/readiness?strategy_id=etf_159915_minute&symbols=159915.SZ&start=YYYY-MM-DD&end=YYYY-MM-DD`

The API validates the registered strategy, symbol count, and ISO date range, then delegates to an ETF readiness service that reads local Parquet metadata and the selected symbol only.

Response shape:

```json
{
  "strategy_id": "etf_159915_minute",
  "ready": true,
  "blocking_reasons": [],
  "warnings": [],
  "coverage": {
    "daily": {},
    "minute": {}
  }
}
```

Blocking conditions:

- No ETF daily data for the selected symbol/range.
- Fewer than 10 completed daily bars before the first requested trading day.
- A trading day present in ETF daily data has no minute rows for `159915.SZ`.
- A minute trading day lacks the essential `09:30` or `15:00` bar.

Warnings:

- A day has fewer than the expected 241 rows.
- `14:59` is missing.
- Stored zero-volume rows are present.

The response includes counts, first/last dates, missing-day samples, sparse-day counts, and zero-volume counts. Samples are capped so the UI payload stays small.

## Frontend Data Flow

1. The user selects 159915 and a valid date range.
2. React Query requests readiness using strategy, symbol, start, and end in the query key.
3. While loading, the run button is disabled for 159915.
4. A blocking response disables the run button and lists exact reasons.
5. Warnings are visible but do not disable the run button.
6. Starting a backtest retains the existing SSE task path.
7. On completion, the execution trace joins `signal_diagnostics` to `fills` by `signal_id` and displays signal time, due time, fill time, direction, rule, status, price, shares, commission, and slippage.

The backend stream repeats the same readiness validation before starting the worker. This prevents direct API calls from bypassing the UI gate.

## Error Handling

- Invalid date or symbol inputs return HTTP 400 with the existing API error style.
- Missing local datasets return a normal readiness payload with `ready=false`, not a server error.
- Parquet schema/read failures return a blocking reason without exposing filesystem internals.
- A readiness request failure renders a compact retry state and keeps run disabled.
- Existing completed results remain visible while readiness is refreshed for a new configuration, but the new run remains gated.

## Testing

Backend tests:

- Complete daily/minute fixtures report ready.
- Insufficient daily warmup blocks.
- A whole missing minute day blocks.
- Missing `09:30` or `15:00` blocks.
- Missing `14:59`, sparse rows, and zero-volume rows warn without blocking.
- API route exists and stream rejects an ETF run when readiness blocks.

Frontend tests:

- Extension registry resolves only for `etf_159915_minute`.
- Rule summary exposes the fixed contract and rule families.
- Readiness helper disables only loading/error/blocking states.
- Execution rows correctly join signals and fills by `signal_id`.
- Generic strategy behavior and existing backtest pool tests remain unchanged.

Verification:

- Run focused backend and frontend tests.
- Run the frontend production build.
- Start the development server and inspect desktop and mobile layouts in the browser.
