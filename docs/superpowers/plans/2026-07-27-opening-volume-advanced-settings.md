# Opening Volume Advanced Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five working advanced-setting tabs to the opening-volume minute portfolio backtest.

**Architecture:** Reuse the existing `params` and `overrides` request schema, then adapt those values into `MinutePortfolioConfig`. Apply filtering and scoring at minute-candidate selection time, risk controls at the T+1 exit stage, and explicit symbols as a watchlist subset.

**Tech Stack:** React 18, TypeScript, Node test runner, FastAPI, Python dataclasses, Polars, pytest.

---

### Task 1: Lock down the opening-volume UI contract

**Files:**
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`
- Modify: `frontend/src/lib/backtestTask.ts`
- Create: `frontend/src/pages/backtest/openingVolumeSettings.test.ts`

- [ ] Add a failing Node test asserting that a `minute_native` opening-volume strategy exposes `params`, `filter`, `scoring`, `risk`, and `range`, but not `entry` or `exit`.
- [ ] Add a failing Node test asserting that minute-portfolio query construction preserves an explicit symbol list.
- [ ] Run `node --test src/pages/backtest/openingVolumeSettings.test.ts` from `frontend`; expect both new assertions to fail.
- [ ] Extract small pure helpers for visible tabs and query symbol serialization, then use them in the page/task code.
- [ ] Re-run the focused Node test file; expect it to pass.

### Task 2: Pass advanced settings through the minute API

**Files:**
- Modify: `backend/app/api/backtest.py`
- Modify: `backend/app/backtest/minute_portfolio.py`
- Modify: `backend/tests/backtest/test_minute_portfolio_api.py`

- [ ] Add a failing API test that sends `symbols`, `basic_filter`, `scoring`, score bounds, and risk overrides and asserts the resulting `MinutePortfolioConfig` values.
- [ ] Run `pytest backend/tests/backtest/test_minute_portfolio_api.py -q`; expect the new test to fail because the endpoint ignores these values.
- [ ] Parse request symbols as a watchlist subset, merge saved and request overrides, validate numeric risk values, and populate new config fields.
- [ ] Re-run the API test file; expect all tests to pass.

### Task 3: Apply basic filtering and scoring to minute candidates

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py`
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/strategy/builtin/opening_volume_portfolio.py`

- [ ] Add failing tests for current-price, cumulative-amount, board and ST filters before ABC evaluation.
- [ ] Add failing tests showing configured weights and score bounds change candidate inclusion/order while empty settings retain `rank_candidates` behavior.
- [ ] Run the focused new pytest node IDs and verify expected failures.
- [ ] Compute cumulative amount, enrich candidates with supported fields, apply filters, calculate 0-100 min-max scores, and preserve deterministic tie-breaking.
- [ ] Declare opening-volume scoring factors in strategy metadata and re-run the focused tests.

### Task 4: Apply minute-native risk controls

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py`
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `frontend/src/components/strategy/OpeningVolumeParamsEditor.tsx`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`

- [ ] Add failing tests for take-profit, trailing-stop, drawdown-take-profit, max-hold exits and priority over the MA exit.
- [ ] Run the focused risk tests and verify they fail for missing behavior.
- [ ] Implement risk checks only after the entry date, queue execution for the next available minute open, and retain existing stop-loss/MA behavior.
- [ ] Bind the risk tab stop-loss field to `strategyParams.stop_loss_pct` and remove it from the common parameter card so only one editable control owns the value.
- [ ] Re-run focused backend and frontend tests.

### Task 5: Regression and browser verification

**Files:**
- Modify only files required by failures found in this task.

- [ ] Run `pytest backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py backend/tests/test_opening_volume_strategy.py -q`.
- [ ] Run the broader backend test suite excluding unavailable optional vn.py tests.
- [ ] Run `pnpm test -- --run` and `pnpm build` from `frontend`.
- [ ] Start the existing frontend/backend development servers if needed and inspect the early-volume advanced-settings dialog at desktop width.
- [ ] Confirm all five tabs are editable, values survive close/reopen in the current page state, and no controls overlap.
