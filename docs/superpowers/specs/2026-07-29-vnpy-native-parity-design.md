# vn.py and Native Minute Portfolio Parity Design

## Objective

Make vn.py a second execution backend for the existing `opening_volume_portfolio`
strategy instead of presenting it as a separate opening-breakout product. The native
and vn.py backends must accept the same configuration and, when given identical
minute bars, produce identical signals, candidate ordering, fills, fees, positions,
and terminal results.

This work deliberately leaves the production data layer unchanged. The native
backend may continue reading a user-selected per-symbol Parquet directory, while
the vn.py backend continues streaming standard date-partitioned Parquet from the
TickFlow repository.

## Confirmed Product Decisions

1. The backtest page has one strategy selection and one shared settings form.
2. `native` and `vnpy` are execution-backend choices, not separate strategies.
3. The page sends the same strategy, portfolio, execution, and risk configuration
   to both backends.
4. The vn.py data-source field is read-only and reports the standard repository
   source and available date range. Direct reading of the external per-symbol
   directory is out of scope.
5. Strategy definitions remain those of the current native opening-volume strategy.
6. Realistic market-execution rules currently present in the vn.py path are shared
   by both backends, even where this changes previous native results.
7. Existing `/vnpy/stream` callers remain compatible, but the main UI uses the
   unified minute-portfolio endpoint.

## Non-Goals

- No conversion or copying of the external per-symbol minute directory.
- No replacement of the standard date-partitioned vn.py data path.
- No optimization of the native Parquet loading path in this change.
- No automatic binding of saved stock-pool snapshots to a backtest.
- No deletion of the legacy vn.py strategy API or its registered strategy IDs.
- No change to unrelated matrix, daily, or single-symbol backtest engines.

## Architecture

The implementation introduces one dependency-free opening-volume business core.
It accepts normalized bars, daily references, positions, and configuration values.
It must not import Polars or vn.py.

```text
Shared opening-volume configuration
  -> Shared signal and execution-policy core
      -> Native dictionary adapter -> native minute executor
      -> vn.py BarData adapter      -> vn.py portfolio executor
```

The shared core owns behavior that must remain identical:

- A/B/C entry evaluation and OR semantics;
- basic filters;
- score calculation and candidate ordering;
- maximum-position selection;
- dynamic cash reserve and target position value;
- buy and sell participation limits;
- stop loss, take profit, trailing exits, maximum holding days, and dynamic MA exit;
- commission, stamp tax, slippage, minimum commission, lot rounding, price limits,
  suspension checks, T+1, order expiry, and terminal handling.

The executors own only time advancement, input-object adaptation, order state, and
result collection. A backend-specific implementation must not silently redefine a
shared rule.

## Shared Configuration Contract

Both backends receive one normalized configuration with these groups:

- Strategy: scan window; A/B/C enablement and branch parameters; stop loss; MA
  period.
- Filters: price, cumulative amount, board, and ST exclusions.
- Ranking: volume-ratio, weighted score, or stock-pool order; score bounds and
  weights.
- Portfolio: initial capital, maximum positions, dynamic cash reserve, and equal
  sizing.
- Execution: entry fill, exit fill, commission, stamp tax, slippage, minimum
  commission, buy participation limit, and sell participation limit.
- Risk: take profit, trailing stop, trailing take profit, and maximum holding days.
- Range: start, end, stock pool, and force-close-at-end.

Ratios use decimal values at the API boundary. For example, `1.0` means 100% of a
minute bar's volume. A participation value of `0` or `null` means unlimited. The UI
shows percentages and performs the conversion once.

## Strategy Rules

The current native `opening_volume_portfolio` behavior is the strategy baseline:

- default scan window is 09:30 through 09:59;
- any enabled A/B/C branch can create an entry candidate;
- A uses the configured previous-candle condition, same-minute cumulative-volume
  ratio, and the native previous-high breakout definition;
- B and C use their configured return and volume thresholds;
- candidate selection uses the user-selected ranking mode, never matched-condition
  count plus random shuffling;
- dynamic MA uses completed prior-day closes plus the current minute close and
  follows the native exit trigger semantics.

Diagnostics may record every matched entry branch, but the number of matches must
not affect ranking unless an explicit shared scoring weight says so.

## Shared Market Rules

Both executors adopt the following market behavior:

- minimum commission is 5 CNY by default;
- T+1 blocks selling shares bought on the same trading day;
- suspended or missing bars cannot trade;
- limit-up bars cannot be bought and limit-down bars cannot be sold;
- board price-limit ratios follow security metadata when available;
- metadata fallback ratios are main board 10%, ChiNext 20%, STAR 20%, and BSE 30%;
- ST fallback is 10%, as explicitly required for this project;
- STAR initial purchases require at least 200 shares; other default initial
  purchases require 100 shares;
- subsequent volume is rounded to the applicable board lot;
- a next-minute order uses the symbol's next actual minute bar in the same trading
  day, including across an intraday missing bar or lunch break;
- an order with no later bar that day expires and must never carry into the next
  trading day.

The buy and sell participation limits are separate editable fields. Both default to
100% of the execution minute's volume. Zero means unlimited. Capacity is rounded
down to the applicable trading lot before filling.

## Cash and Position Sizing

The shared equal-sizing rule uses current portfolio equity, not initial capital:

```text
reserve cash = current equity * reserve ratio
target value per slot = current equity * (1 - reserve ratio) / max positions
spendable cash = max(current cash - reserve cash, 0)
```

The calculation is refreshed before each entry decision so both backends use the
same equity mark and available cash. The default reserve remains 3%, but the value
comes from the shared form rather than a vn.py-only hard-coded parameter.

## API Design

The main page calls the existing minute-portfolio stream with an explicit backend:

```text
GET /api/backtest/minute-portfolio/stream?engine=native
GET /api/backtest/minute-portfolio/stream?engine=vnpy
```

One parser validates and normalizes all shared settings before dispatch. Job keys
include the engine and every effective setting. Progress and cancellation retain the
existing SSE contract.

`/api/backtest/vnpy/stream` remains available as a compatibility adapter. It maps
legacy parameters into the shared configuration and delegates to the same vn.py
execution path. It must not maintain a second copy of defaults or business rules.

## Frontend Design

The backtest page keeps one `opening_volume_portfolio` strategy selection. A compact
segmented control selects `native` or `vnpy`. Switching engines preserves all form
values.

Both engines show the same sections:

1. Stock pool and date range.
2. A/B/C strategy cards.
3. Basic filters.
4. Scoring weights and candidate ordering.
5. Risk controls.
6. Entry and exit fill rules.
7. Capital, maximum positions, dynamic cash reserve, fees, and slippage.
8. Buy and sell minute-volume participation limits.
9. Force close at end.

The vn.py-only strategy selector and fixed 10% checkbox are removed from the main
page. In vn.py mode the minute-data source is read-only and displays the repository
source plus available date range. In native mode the existing editable external
directory field remains.

## Result Contract and Errors

Both backends return the same top-level result shape for metrics, equity, drawdown,
completed trades, open positions, per-symbol statistics, signal diagnostics,
rejections, and execution counters. Backend-specific fields may be retained under a
namespaced diagnostic object, but the result page must not branch for ordinary
portfolio reporting.

Expected failures are reported before the worker starts where possible:

- no symbols;
- invalid date range;
- invalid percentage or fee;
- unavailable vn.py minute partitions for the requested range;
- missing external native data directory;
- unsupported fill or ranking mode.

The vn.py no-data error must include the effective standard data source and its
available date range when known.

## Compatibility and Migration

- Preserve both old API routes while moving the main UI to the unified route.
- Preserve registered legacy vn.py strategy IDs for API callers.
- Preserve saved native strategy overrides and map them directly into the shared
  configuration.
- Ignore no legacy parameter silently. Return a validation error or record an
  explicit compatibility mapping.
- Do not remove existing native result fields.

## Verification

Tests use one small deterministic minute dataset and submit the same normalized
configuration to both backends.

Required parity assertions:

- entry and exit signal timestamps and reasons;
- matched branches and filtered candidates;
- candidate order and maximum-position selection;
- order expiry and rejection reasons;
- fill timestamps, prices, volume, commission, tax, and slippage;
- cash, positions, realized PnL, unrealized PnL, and terminal equity;
- force-close and non-force-close behavior;
- T+1, suspension, price-limit, STAR minimum, ST 10%, and lot rounding;
- buy and sell participation values of 100%, another positive value, and unlimited;
- dynamic 3% cash reserve after equity changes.

API tests cover shared validation, dispatch, SSE progress, cancellation, and legacy
vn.py compatibility. Frontend tests cover engine switching without form reset,
identical settings visibility, read-only vn.py data-source status, request payloads,
and removal of the fixed 10% control.

Production parity is only expected when the native and standard vn.py stores contain
identical minute bars. The test suite proves engine parity independently of local
data-source differences.

## Delivery Sequence

1. Add shared configuration, normalized views, market rules, and pure rule tests.
2. Adapt the native executor to the shared core while retaining its API behavior.
3. Adapt the vn.py strategy and portfolio executor to the shared core.
4. Add unified endpoint dispatch and legacy vn.py compatibility mapping.
5. Replace the vn.py-specific frontend form with the shared form and data status.
6. Add cross-engine parity, API, and frontend regression tests.

Each step must keep the existing native and legacy vn.py paths runnable. No step may
delete an old route or strategy registration merely because the main UI no longer
uses it.

## Success Criteria

- One shared page configures both execution backends.
- Both backends consume one normalized configuration contract.
- No opening-volume business rule is duplicated between the native and vn.py paths.
- Identical test data and settings produce identical observable results.
- Realistic shared market rules, including ST 10% and separate buy/sell volume
  limits, apply to both backends.
- The vn.py backend keeps its date-partition streaming data path.
- Existing API callers and unrelated backtest engines remain compatible.
