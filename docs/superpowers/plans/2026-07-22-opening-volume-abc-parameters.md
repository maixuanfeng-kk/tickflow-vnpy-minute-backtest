# Opening Volume ABC Parameters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every existing A/B/C entry condition editable in both strategy and backtest settings, with independent per-branch volume rules that affect execution.

**Architecture:** Extend the built-in strategy metadata as the shared UI schema, and extend `OpeningVolumeStrategyParams` as the single validated runtime contract. Keep the generic frontend editors unchanged unless the expanded schema exposes a concrete rendering defect.

**Tech Stack:** Python dataclasses and pytest; React, TypeScript, and Vite.

---

### Task 1: Lock the runtime contract with failing tests

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`

- [ ] **Step 1: Add independent-volume tests**

Add tests that configure different A/B/C volume multiples and assert a candidate can fail one branch's volume threshold while passing another branch.

- [ ] **Step 2: Add volume-switch tests**

Add a test with a branch volume condition disabled and a below-threshold volume ratio, then assert the branch can still trigger when its remaining conditions pass.

- [ ] **Step 3: Add editable-condition tests**

Cover A candle direction and breakout requirement, B current/previous return bounds, and C candle direction/previous return bound.

- [ ] **Step 4: Run tests and verify RED**

Run: `python -m pytest backend/tests/backtest/test_minute_portfolio.py -q`

Expected: FAIL because the new constructor fields and behavior do not exist.

### Task 2: Implement the runtime contract

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py`
- Test: `backend/tests/backtest/test_minute_portfolio.py`

- [ ] **Step 1: Extend `OpeningVolumeStrategyParams`**

Add the branch-specific switches, multiples, direction choices, breakout requirement, and return bounds. Retain `volume_multiple` only as a legacy fallback.

- [ ] **Step 2: Validate mappings**

Parse booleans and percentages through the existing mapping path. Reject unsupported candle directions, non-positive volume multiples, and an invalid B lower/upper range.

- [ ] **Step 3: Parameterize `entry_reason`**

Evaluate A, B, and C in their existing order. Each branch must independently apply its own optional volume condition and its enabled internal conditions.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest backend/tests/backtest/test_minute_portfolio.py -q`

Expected: PASS.

### Task 3: Expose the shared parameter schema

**Files:**
- Modify: `backend/app/strategy/builtin/opening_volume_portfolio.py`
- Modify: `backend/tests/test_opening_volume_strategy.py`
- Modify: `backend/tests/backtest/test_minute_portfolio_api.py`

- [ ] **Step 1: Write failing metadata and propagation assertions**

Assert the strategy metadata declares every branch field and no longer declares the global `volume_multiple`. Assert scan and backtest APIs construct runtime parameters from a branch-specific request value.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest backend/tests/test_opening_volume_strategy.py backend/tests/backtest/test_minute_portfolio_api.py -q`

Expected: FAIL because metadata and API assertions still use the old global field.

- [ ] **Step 3: Replace the metadata schema**

Declare the expanded fields in A/B/C order, using `bool`, `float`, `percent`, and `select` types already supported by both frontend parameter editors.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest backend/tests/test_opening_volume_strategy.py backend/tests/backtest/test_minute_portfolio_api.py -q`

Expected: PASS.

### Task 4: Regression verification

**Files:**
- No production files added.

- [ ] **Step 1: Run focused backend tests**

Run: `python -m pytest backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py backend/tests/test_opening_volume_strategy.py -q`

Expected: PASS.

- [ ] **Step 2: Run frontend production build**

Run from `frontend`: `pnpm build`

Expected: exit code 0.

- [ ] **Step 3: Inspect the final diff**

Run: `git diff --check` and `git diff -- backend/app/backtest/minute_portfolio.py backend/app/strategy/builtin/opening_volume_portfolio.py backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py backend/tests/test_opening_volume_strategy.py`

Expected: no whitespace errors and no changes outside the approved scope.
