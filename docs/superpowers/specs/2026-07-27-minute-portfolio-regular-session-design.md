# Minute Portfolio Regular-Session Execution Design

## Goal

Prevent pre-open minute bars from triggering portfolio execution, stop-loss exits, or moving-average exits in the opening-volume backtest.

## Scope

The minute portfolio engine will process trading rows only from 09:30 onward. Pre-open rows remain available while data context is constructed:

- Daily bars continue to aggregate from 09:25 so the official auction open is preserved.
- Intraday cumulative volume continues to include the 09:25 auction volume.
- Entry scanning remains controlled by the strategy's existing scan window.
- Bars before 09:30 cannot execute pending orders, mark positions, trigger exits, or update portfolio snapshots.

This change does not alter signal ranking, breakout boundaries, cash sizing, end-of-backtest liquidation, or report timestamps.

## Implementation

After `_load_rows_and_context` calculates daily context and cumulative volume, `MinutePortfolioService.run` will trim executable `raw_rows` to the configured date range and to times at or after 09:30. This keeps context calculation unchanged while giving `MinutePortfolioEngine` only executable-session rows.

Filtering at the service boundary is preferred over adding conditions to each engine phase because it produces one explicit execution-data contract and prevents future engine phases from accidentally consuming pre-open bars.

## Tests

Add a regression test at the service boundary using a repository that returns:

1. A held position whose price breaches the stop-loss threshold before 09:30.
2. A regular-session bar that still breaches the threshold.
3. A following regular-session bar where the pending sell can execute.

The test must fail before the implementation because the exit occurs before 09:30. It must pass afterward by proving the exit executes on the first eligible bar after the regular-session signal.

Existing tests must continue to verify that 09:25 auction data contributes to daily aggregation and cumulative volume.

## Compatibility

No API or configuration fields change. Existing callers continue to use `MinutePortfolioService.run`. Backtest results can change because previously invalid pre-open exits are removed; this behavioral change is intentional.
