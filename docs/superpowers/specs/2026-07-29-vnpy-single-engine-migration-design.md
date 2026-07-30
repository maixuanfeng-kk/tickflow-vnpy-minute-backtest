# vn.py Single-Engine Opening-Volume Migration Design

## Objective

Replace the native opening-volume minute portfolio implementation with the vn.py
portfolio runner. The finished product has one opening-volume backtest page, one
vn.py execution path, and the visual structure already used by the native page.
The user does not choose an engine.

## Confirmed Decisions

- vn.py is the only production minute portfolio executor.
- The final page retains the existing native page's layout, configuration sections,
  labels, and workflow; only the executor changes.
- The data layer remains vn.py's standard date-partitioned local Parquet store.
- External per-symbol minute-directory input is removed from the final UI and API.
- The current native A/B/C strategy behavior remains the strategy definition.
- Shared A-share execution uses minimum commission 5 CNY, metadata price limits,
  ST fallback 10%, STAR first-buy minimum 200 shares, T+1, suspension/limit blocks,
  same-day next-actual-bar expiry, and independent buy/sell participation limits.
- No native engine, endpoint, user-facing toggle, local-directory reader, or native
  engine test remains after migration verification passes.

## Final Architecture

```text
StrategyBacktest (native-style UI)
  -> /api/backtest/vnpy/stream
      -> VnpyMinuteBacktestService
          -> standard date-partition Parquet repository
          -> BarData adapter
          -> opening-volume shared rules
          -> MultiSymbolNextBarOpenEngine
          -> unified portfolio result
```

`app.backtest.opening_volume_shared` owns dependency-free business rules. The vn.py
strategy adapts `BarData` and daily references into those rules. The portfolio runner
owns matching, cash, positions, fees, market constraints, and result diagnostics.

## Native-Style Frontend

The page keeps the existing strategy selection and configuration layout:

1. Stock pool and backtest range.
2. A/B/C parameter cards.
3. Basic filters.
4. Score weights and candidate ranking.
5. Risk and exit controls.
6. Entry and exit fill rules.
7. Initial capital, maximum positions, cash reserve, fees, and slippage.
8. Buy and sell minute-volume participation percentages.
9. Force close at backtest end.

The engine selector, vn.py strategy selector, 10% fixed-volume checkbox, and external
minute-directory field are removed. The page instead shows the standard local minute
data availability state and the selected range. Switching strategy settings must not
reset any value because there is no longer a second engine mode.

## Backend Scope

`/vnpy/stream` becomes the only opening-volume minute portfolio stream. It accepts
the full shared opening-volume request contract and returns the same result fields
the existing page needs: metrics, curves, completed trades, open positions,
per-symbol statistics, signal diagnostics, rejections, and execution counters.

The vn.py service maps all strategy parameters, filters, ranking settings, portfolio
settings, risk controls, fill rules, and terminal behavior into the vn.py strategy and
executor. It must never use matched-condition count or random shuffling as a ranking
rule for this strategy.

The native classes and native stream endpoint are removed only after vn.py tests
cover their replacement behavior. The obsolete `opening_breakout_pool` and
`opening_breakout_condition_1` identifiers are replaced by the single
`opening_volume_portfolio` vn.py registration; no legacy compatibility API is kept.

## Data and Error Handling

vn.py continues to read standard local partitions:

```text
<TickFlow data root>/kline_minute/date=YYYY-MM-DD/part.parquet
```

When no selected partition contains any requested symbol, the API returns an error
that identifies the standard source and its available date range. The UI renders this
as a recoverable configuration error rather than a generic connection failure.

## Deletion Gate

Before deleting native code, tests must prove the vn.py path covers:

- all configured A/B/C entry branches;
- filters, score/volume/watchlist candidate ranking, and maximum-position selection;
- entry and exit fills, market constraints, fees, and participation limits;
- dynamic reserve, risk exits, T+1, force close, open positions, and diagnostics;
- standard-minute no-data reporting and SSE progress;
- the native-style frontend request and result rendering.

Once these checks pass, delete the native module, its route, directory reader, engine
toggle, native-only tests, and obsolete imports in one dedicated deletion commit.

## Verification

- Targeted backend pytest suites for vn.py strategy, executor, service, and API.
- Existing frontend opening-volume settings tests plus `pnpm build`.
- One deterministic end-to-end vn.py fixture covering A/B/C, ranking, a fill, an
  exit, and force-close behavior.
- A static search confirming no production import or route points to
  `minute_portfolio`, `LocalMinuteParquetRepository`, `engineMode`, or
  `minuteDataDir` after the deletion commit.

## Out of Scope

- Importing an external per-symbol Parquet directory into standard partitions.
- Supporting two minute engines after migration.
- Preserving callers of the removed native or legacy vn.py strategy endpoints.
