# Opening Volume ABC Layout Correction Design

## Scope

Correct the opening-volume parameter semantics and presentation in both the strategy settings page and the backtest settings page. This correction replaces the flat parameter list with three branch cards and removes controls for conditions that are mandatory parts of the strategy.

Basic filters, scoring, additional risk controls, backtest scope, and execution timing remain outside this correction.

## Parameter Semantics

The public parameter schema contains four common settings:

- scan start time
- scan end time
- stop-loss percentage
- moving-average exit period

Branch A contains:

- branch enabled
- A volume multiple
- previous-day candle direction
- a read-only rule stating that price must cross the previous-day high

Branch B contains:

- branch enabled
- B volume multiple
- current-day return lower bound
- current-day return upper bound
- previous-day return upper bound

Branch C contains:

- branch enabled
- C volume multiple
- previous-day candle direction
- previous-day return upper bound

There are no per-branch volume-condition switches. Each enabled branch always requires its own volume multiple. Branch A always requires crossing the previous-day high and does not expose a switch for that rule.

## Layout

Both editors render the same structure:

1. A compact common-settings card.
2. An A branch card with its enable switch, editable fields, and fixed breakout rule.
3. A B branch card with its enable switch and editable return conditions.
4. A C branch card with its enable switch and editable candle/return conditions.

Disabling a branch visually dims its editable controls but preserves their values. Re-enabling the branch restores those values.

The two pages reuse one opening-volume parameter editor component. Generic strategy parameters continue using the existing generic editors.

## Compatibility

Previously saved `enable_branch_a_volume_filter`, `enable_branch_b_volume_filter`, `enable_branch_c_volume_filter`, and `branch_a_require_previous_high_breakout` values are ignored. They are not displayed and cannot weaken the corrected mandatory rules.

The legacy global `volume_multiple` remains a backend-only fallback when old saved configurations have no branch-specific multiples. It is not displayed in either editor.

## Data Flow

The strategy metadata defines only editable fields. Each page passes the metadata and current values to the shared opening-volume editor. The existing save and backtest request paths remain unchanged, so strategy-page values persist while backtest-page values stay local to that run.

The backend runtime always checks the configured branch volume multiple and always checks the A breakout rule. Strategy scanning and portfolio backtesting continue to call the same entry predicate.

## Validation and Testing

- Runtime tests prove that no enabled branch can bypass its volume multiple.
- Runtime tests prove that Branch A cannot bypass the previous-high breakout.
- Metadata tests prove the removed switch fields are absent.
- Component tests, when supported by the existing frontend test setup, verify card grouping and disabled-branch presentation; otherwise browser UI verification covers both pages.
- Focused backend tests, frontend production build, API detail inspection, and browser console checks complete the regression verification.
