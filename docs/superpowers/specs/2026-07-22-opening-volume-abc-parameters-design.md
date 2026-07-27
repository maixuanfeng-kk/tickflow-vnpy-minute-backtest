# Opening Volume ABC Parameters Design

## Scope

This delivery exposes the existing A, B, and C opening-volume entry rules as editable strategy parameters. It applies equally to the strategy settings page, strategy-page scanning, and the backtest settings page.

Basic filters, scoring, additional risk controls, backtest scope, and entry/exit fill timing are explicitly outside this delivery.

## Entry Semantics

The enabled branches are combined with OR. Conditions inside each enabled branch are combined with AND.

Each branch owns its volume condition:

- The branch volume-condition switch decides whether that branch checks volume.
- The branch volume multiple is used only by that branch.
- A disabled volume condition does not compare the signal-minute volume ratio.

Branch A exposes:

- branch enabled
- volume condition enabled and volume multiple
- previous-day candle direction: bearish, bullish, or unrestricted
- whether crossing the previous-day high is required

Branch B exposes:

- branch enabled
- volume condition enabled and volume multiple
- current-day return lower and upper bounds
- previous-day return upper bound

Branch C exposes:

- branch enabled
- volume condition enabled and volume multiple
- previous-day candle direction: bearish, bullish, or unrestricted
- previous-day return upper bound

Existing inequalities remain unchanged by default: B current-day return is strictly between 3% and 5%, and B/C previous-day return is strictly below 5%. Volume thresholds remain inclusive.

## Compatibility

The strategy metadata no longer presents a global volume multiple. The backend continues to accept the legacy `volume_multiple` value and uses it as the fallback for any branch without its own stored multiple. This preserves existing saved strategy configurations while ensuring all newly edited configurations are branch-specific.

## Data Flow

The built-in strategy metadata remains the single UI schema. Both existing parameter editors read it and submit their values through the current `params` payload. The strategy scan API merges saved parameters with request parameters, and the minute backtest API merges saved parameters with temporary backtest parameters before constructing `OpeningVolumeStrategyParams`.

## Validation

- Candle direction accepts only the three declared choices.
- Volume multiples must be positive.
- B current-day lower bound must be less than its upper bound.
- Return bounds are entered and transported as decimal percentages, matching existing `percent` parameter behavior.
- Existing scan time, stop-loss, and moving-average validation remains unchanged.

## Verification

Backend tests cover independent branch volume thresholds, disabled volume conditions, editable A/B/C conditions, invalid parameter combinations, metadata defaults, scan parameter propagation, and backtest parameter propagation. The frontend production build verifies both generic parameter editors accept the expanded metadata schema.
