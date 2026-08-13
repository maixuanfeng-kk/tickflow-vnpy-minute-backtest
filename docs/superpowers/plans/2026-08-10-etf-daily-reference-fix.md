# ETF Daily Reference Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the 159915 strategy uses authoritative ETF daily OHLC and pre-close values for completed-day references while continuing to use completed minute closes for intraday signals.

**Architecture:** Keep `DailyContextBuilder` unchanged because it already accepts official daily prices. Route ETF backtests to `kline_etf_daily` inside the service metadata loader, while stock strategies retain the existing stock daily source selection.

**Tech Stack:** Python, Polars, pytest, vn.py backtest service.

---

### Task 1: Reproduce the ETF daily-source regression

**Files:**
- Modify: `backend/tests/vnpy_backtest/test_etf_159915_data_flow.py`

- [x] Add a test that writes conflicting ETF daily and minute lows, loads ETF daily metadata through `VnpyMinuteBacktestService`, feeds it to `DailyContextBuilder`, and expects `previous_low` to equal the ETF daily low.
- [x] Run the test and verify it fails because the current metadata loader does not select `kline_etf_daily`.

### Task 2: Select the ETF daily dataset

**Files:**
- Modify: `backend/app/vnpy_backtest/service.py`

- [x] Add an ETF flag to `_daily_limit_metadata` and select `kline_etf_daily` for ETF backtests.
- [x] Pass the ETF flag from `_run_portfolio`.
- [x] Preserve the existing stock Tushare/XBX fallback unchanged.
- [x] Run the regression test and verify it passes.

### Task 3: Verify and rerun the backtest

**Files:**
- No production file changes.

- [x] Run focused ETF strategy, data-flow, readiness, importer, service, and portfolio tests.
- [x] Reproduce the 2025-01-10 signal and verify 14:25 signal / 14:26 fill.
- [x] Run 2025-01-01 through 2025-12-31 with initial capital 1,000,000 CNY, commission rate 0.00012, slippage 1 bps, and ETF stamp tax 0.
- [x] Record summary metrics and the result artifact path.
