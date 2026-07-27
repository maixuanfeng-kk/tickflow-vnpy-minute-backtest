# Dynamic Portfolio Cash Reserve Design

## Goal

Change minute-portfolio buy sizing so `cash_reserve_ratio=0.03` means the
portfolio keeps approximately 3% of its current marked-to-market equity in
cash, while at most 97% of current equity is allocated across the configured
maximum number of positions.

For a 10-position portfolio, each position therefore has a target market value
of approximately 9.7% of current equity.

## Scope

This change affects only buy sizing in `MinutePortfolioEngine`.

It does not change:

- opening-volume entry conditions or candidate ranking;
- enabled A/B/C branches;
- sell signals or end-of-backtest behavior;
- next-minute-open execution;
- minute-volume participation limits;
- board-lot rounding, commission, stamp tax, or slippage.

Consequently, this change will not make the selected stocks match the older
BulletTrade report. Entry-signal differences must be handled separately.

## Buy-Sizing Rule

Immediately before each pending buy is filled, calculate current portfolio
equity as:

```text
cash
+ current marked value of every existing position
```

Use the current timestamp's bar price for an existing position when available;
otherwise fall back to its latest known close and then its entry price.

Then calculate:

```text
reserve_cash = current_equity * cash_reserve_ratio
investable_equity = current_equity * (1 - cash_reserve_ratio)
target_position_value = investable_equity / max_positions
spendable_cash = max(cash - reserve_cash, 0)
```

The preliminary share count is based on the smaller of
`target_position_value` and `spendable_cash`, including buy commission in the
all-in unit price. The existing minute-volume limit is applied next, and the
result is finally rounded down to the configured board lot.

When `cash_reserve_ratio=0`, behavior remains backward compatible: the target
position value is current equity divided by `max_positions`, with no cash
floor.

## Multiple Fills at One Timestamp

Pending buys continue to execute sequentially in deterministic candidate
order. Portfolio equity and the cash floor are recalculated before every fill.
Commissions and lot rounding can leave slightly more than 3% cash; a buy must
never reduce cash below the calculated floor.

## Verification

Tests will prove:

1. Ten simultaneous fills with a 3% reserve allocate at most 97% of equity and
   leave at least 3% cash.
2. The per-position target follows current marked-to-market equity rather than
   remaining fixed to initial capital.
3. The minute-volume cap can still impose the stricter share limit.
4. The default zero-reserve configuration preserves existing behavior.
5. The complete minute-portfolio, API, and opening-volume regression set stays
   green.

## Result Interpretation

The reserve is based on current portfolio equity at execution time, not a fixed
3% of initial capital. It is approximate from the user's perspective because
board-lot rounding and fees normally leave a small amount of extra cash.
