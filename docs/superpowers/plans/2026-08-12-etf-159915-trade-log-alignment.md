# 159915 Trade Log Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 159915 strategy reproduce the supplied trade log's ordered signal chain as closely as the local daily and minute data permit.

**Architecture:** Keep the existing single strategy class and add only the state required by the log: special-buy setup provenance, sell-protection provenance, and confirmed 5.3 setup consumption. Compare signals from the start of 2025 after every behavior change; a change is accepted only when the first mismatch moves later or becomes a reason-only difference.

**Tech Stack:** Python 3, vn.py strategy contracts, pytest, Polars-backed local ETF market data.

---

### Task 1: Lock the first two divergence chains

**Files:**
- Modify: `backend/tests/vnpy_backtest/test_etf_159915_strategy.py`

- [ ] **Step 1: Add a failing 4.2.3-2 carry-over test**

Construct consecutive strategy days. Day one establishes a level-2 special setup but never crosses its trigger. Day two first presents an ordinary 4.1.1 price that must be suppressed, then crosses the retained trigger and expects reason `4_2_3_2_before`.

- [ ] **Step 2: Run the focused buy test and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\vnpy_backtest\test_etf_159915_strategy.py -k "4232_carries" -q
```

Expected: FAIL because current level-2 state expires before the next session.

- [ ] **Step 3: Add a failing previous-5.2 provenance test**

Use a strong a-2 day and neutral a-1 day. Verify the retained trigger suppresses ordinary exits above the trigger and emits `5_2_before_2_2` only after a strict break.

- [ ] **Step 4: Run the focused sell test and verify RED**

Run the test by name. Expected: FAIL because current code emits generic `5.2`.

### Task 2: Implement buy setup provenance and time windows

**Files:**
- Modify: `backend/app/vnpy_backtest/strategies/etf_159915_minute.py`
- Modify: `backend/tests/vnpy_backtest/test_etf_159915_strategy.py`

- [ ] **Step 1: Track whether the active special setup was formed today**

Add one boolean state field. Reset it before the daily refresh, set it only when the current daily reference forms a new setup, and retain the trigger for two candidate sessions for both levels.

- [ ] **Step 2: Emit provenance-aware log reasons**

Use `4_2_3_1`/`4_2_3_2` for a fresh setup and `4_2_3_1_before`/`4_2_3_2_before` for a retained setup.

- [ ] **Step 3: Apply the chart/log buy windows**

Evaluate special buys during `09:31-11:30` and `14:45-14:59`. Preserve their exclusive short-circuit over ordinary morning and tail rules.

- [ ] **Step 4: Run all strategy unit tests**

Expected: the new tests pass. Update older tests only where they encode a conflicting 09:30 special signal or legacy reason punctuation.

### Task 3: Implement sell protection provenance and lifecycle

**Files:**
- Modify: `backend/app/vnpy_backtest/strategies/etf_159915_minute.py`
- Modify: `backend/tests/vnpy_backtest/test_etf_159915_strategy.py`

- [ ] **Step 1: Add failing tests for fresh 5.2 and fresh 5.3**

Fresh a-1 5.2 must emit `5_2`; fresh a-2/a-1 5.3 must emit `5_3`. Continued states must use the corresponding `before` reason.

- [ ] **Step 2: Return trigger, reason, and setup identity from protection lookup**

Check fresh 5.3 before older 5.3 states, then fresh 5.2 before old 5.2. Any active protection short-circuits 5.1, 5.5, and 5.6 even without a sell.

- [ ] **Step 3: Restore fill-confirmed 5.3 consumption**

Record the pending setup on a 5.3 intent. Consume it only after the observed position changes from held to flat. Do not fall back to older 5.3 setups after the newest setup was consumed.

- [ ] **Step 4: Run protection tests and the full strategy test file**

Expected: all tests pass and the September old-setup/new-position regression remains covered.

### Task 4: Restore log-proven behavior omitted from the image

**Files:**
- Modify: `backend/app/vnpy_backtest/strategies/etf_159915_minute.py`
- Modify: `backend/tests/vnpy_backtest/test_etf_159915_strategy.py`

- [ ] **Step 1: Add failing tests for 09:31 sell lower bound, 5.4 re-entry, and 5.6 decline**

Verify no 09:30 sell, a 5.4 exit can re-enter above the day open with reason `5_4`, and a drop strictly greater than 1% from previous close can emit `5_6` without breaking either prior low.

- [ ] **Step 2: Implement the minimum behavior**

Use the overall `09:31-14:57` sell window. Restore only 5.4 same-day re-entry and the proven second 5.6 clause.

- [ ] **Step 3: Normalize all diagnostic reasons**

Use CSV labels: `4_1_1`, `4_1_2`, `4_2_1`, `4_2_2`, `4_3_1`, `4_3_2`, `4_3_3`, `5_1_1`, `5_1_2`, `5_1_3`, `5_2`, `5_3`, `5_4`, `5_5`, and `5_6` plus the explicit `before` variants.

- [ ] **Step 4: Run the complete vn.py ETF test subset**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\vnpy_backtest\test_etf_159915_strategy.py backend\tests\vnpy_backtest\test_etf_159915_execution.py backend\tests\vnpy_backtest\test_etf_159915_data_flow.py -q
```

### Task 5: Replay and measure alignment

**Files:**
- Modify only if a newly proven first-divergence cause requires it: `backend/app/vnpy_backtest/strategies/etf_159915_minute.py`
- Test the same behavior in: `backend/tests/vnpy_backtest/test_etf_159915_strategy.py`

- [ ] **Step 1: Run the 2025 backtest with zero slippage**

Preserve next-minute-open execution, 97% exposure, and configured commission. Save a new result without overwriting prior result artifacts.

- [ ] **Step 2: Compare ordered diagnostics to the 2025 reference log**

Normalize only punctuation aliases. Report exact timestamp/direction/reason matches, direction/time matches, the first mismatch, and total local/reference counts.

- [ ] **Step 3: Repeat one-divergence TDD cycles**

For every next mismatch, prove the applicable daily/minute inputs, add one failing test, implement one minimal correction, and rerun from the start. Do not tune thresholds solely to increase return.

- [ ] **Step 4: Run final verification**

Run the focused ETF suite and report the verified zero-slippage 2025 return separately from signal alignment.
