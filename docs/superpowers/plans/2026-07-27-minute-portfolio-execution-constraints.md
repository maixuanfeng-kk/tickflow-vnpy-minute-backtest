# Minute Portfolio Execution Constraints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in cash-reserve, minute-volume, and dynamic intraday MA5 constraints to the opening-volume minute portfolio engine, then run the approved 609-symbol backtest.

**Architecture:** Preserve the existing `MinutePortfolioConfig -> MinutePortfolioEngine -> MinutePortfolioService` path. Add two backward-compatible execution fields to the config, calculate constrained shares only at the existing next-minute-open fill point, and attach the four prior daily closes to each date context so the existing exit loop can calculate an intraday MA5 without future data.

**Tech Stack:** Python 3.11+, dataclasses, Polars, pytest, existing local pytdx Parquet repository.

---

## File Map

- Modify `backend/app/backtest/minute_portfolio.py`: configuration, context preparation, buy sizing, dynamic MA5, result audit fields.
- Modify `backend/tests/backtest/test_minute_portfolio.py`: regression tests for all new behavior and default compatibility.
- Generate `.tmp/backtests/opening-volume-2026-01-06_2026-01-30.json`: complete local backtest result; do not commit it.

### Task 1: Cash Reserve and Minute-Volume Buy Limits

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py:249-261`
- Modify: `backend/app/backtest/minute_portfolio.py:474-496`
- Test: `backend/tests/backtest/test_minute_portfolio.py`

- [ ] **Step 1: Write failing buy-sizing tests**

Add tests that create an A-branch signal at 09:30 and fill at 09:31. Assert the trade has 9,700 shares for `initial_capital=100_000`, zero costs, price 10, and `cash_reserve_ratio=0.03`; assert 500 shares for execution-minute volume 550 and `max_buy_volume_ratio=1.0`; assert the smaller limit wins when both fields are enabled; assert the existing default still produces 10,000 shares when both fields use their defaults.

```python
config = MinutePortfolioConfig(
    symbols=["600000.SH"],
    initial_capital=100_000.0,
    max_positions=1,
    commission_pct=0.0,
    slippage_bps=0.0,
    cash_reserve_ratio=0.03,
    max_buy_volume_ratio=1.0,
)
result = MinutePortfolioEngine(config).run(rows, contexts)
assert result["trades"][0]["shares"] == expected_shares
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio.py -k "cash_reserve or buy_volume" -q
```

Expected: FAIL because `MinutePortfolioConfig` does not accept `cash_reserve_ratio` or `max_buy_volume_ratio`.

- [ ] **Step 3: Add backward-compatible config fields and minimal sizing logic**

Add:

```python
cash_reserve_ratio: float = 0.0
max_buy_volume_ratio: float | None = None
```

At the existing pending-buy fill point calculate:

```python
spendable_cash = cash * (1 - self.config.cash_reserve_ratio)
all_in_price = price * (1 + self.config.commission_pct)
shares = floor(min(target, spendable_cash) / all_in_price)
if self.config.max_buy_volume_ratio is not None:
    shares = min(
        shares,
        floor(float(bar["volume"]) * self.config.max_buy_volume_ratio),
    )
shares = shares // self.config.lot_size * self.config.lot_size
```

- [ ] **Step 4: Run focused and full minute-portfolio tests**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio.py -q
```

Expected: all tests PASS, including the default-compatibility assertion.

- [ ] **Step 5: Commit the isolated behavior**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "feat(backtest): constrain minute portfolio buy fills"
```

### Task 2: Dynamic Intraday MA5 Exit

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py:330-389`
- Modify: `backend/app/backtest/minute_portfolio.py:514-526`
- Test: `backend/tests/backtest/test_minute_portfolio.py`

- [ ] **Step 1: Write failing context and exit tests**

Add one context-preparation test asserting that the fifth trading date receives the preceding four closes. Add an engine test where static `previous_ma5=9.0` would not exit but prior closes `[11.0, 11.0, 11.0, 11.0]` and current close `10.1` create dynamic MA5 `10.82`, producing `ma5_breakdown`. Add a test with only three prior closes and assert the position exits only as `end_of_backtest`.

```python
contexts[(symbol, day)] = {
    "previous_ma5": 9.0,
    "previous_closes": [11.0, 11.0, 11.0, 11.0],
}
assert result["trades"][0]["exit_reason"] == "ma5_breakdown"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio.py -k "intraday_ma5 or previous_closes" -q
```

Expected: FAIL because contexts do not expose `previous_closes` and the exit loop still uses the static prior-day MA5.

- [ ] **Step 3: Populate four prior closes and calculate dynamic MA5**

In `_load_rows_and_context`, add:

```python
"previous_closes": [float(row["close"]) for row in prior[-4:]],
```

In the exit loop use the dynamic formula only for period 5:

```python
if self.config.strategy_params.ma_exit_period == 5:
    previous_closes = list(context.get("previous_closes") or []) if context else []
    ma_value = (
        (sum(previous_closes[-4:]) + close) / 5
        if len(previous_closes) >= 4 and close > 0
        else 0.0
    )
else:
    ma_key = f"previous_ma{self.config.strategy_params.ma_exit_period}"
    ma_value = float(context.get(ma_key) or 0) if context else 0.0
```

- [ ] **Step 4: Run focused tests and all minute-portfolio tests**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio.py -q
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio_api.py -q
```

Expected: both test files PASS.

- [ ] **Step 5: Commit the isolated MA behavior**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "fix(strategy): calculate intraday minute portfolio ma5"
```

### Task 3: Audited 609-Symbol Backtest Run

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py:881-891`
- Generate: `.tmp/backtests/opening-volume-2026-01-06_2026-01-30.json`

- [ ] **Step 1: Include execution constraints in the result config**

Add `cash_reserve_ratio` and `max_buy_volume_ratio` to `result["config"]` so the saved result proves which constraints were active. Extend the existing service-shape test to assert both values.

- [ ] **Step 2: Run the complete relevant regression set**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio.py tests/backtest/test_minute_portfolio_api.py tests/test_opening_volume_strategy.py -q
```

Expected: all tests PASS.

- [ ] **Step 3: Run the approved backtest**

From `backend`, instantiate `DataStore`, `KlineRepository`, and `MinutePortfolioService`, read the 609-symbol watchlist, and run:

```python
MinutePortfolioConfig(
    symbols=symbols,
    start=date(2026, 1, 6),
    end=date(2026, 1, 30),
    initial_capital=10_000_000.0,
    max_positions=10,
    commission_pct=0.00012,
    stamp_tax_pct=0.0005,
    slippage_bps=1.0,
    cash_reserve_ratio=0.03,
    max_buy_volume_ratio=1.0,
    minute_data_dir=r"F:\quant\data\minute_1min_pytdx",
)
```

Write the JSON result to `.tmp/backtests/opening-volume-2026-01-06_2026-01-30.json` and print progress plus final statistics.

- [ ] **Step 4: Verify the result**

Assert and report:

```text
watchlist count = 609
config.initial_capital = 10000000
config.max_positions = 10
config.cash_reserve_ratio = 0.03
config.max_buy_volume_ratio = 1.0
all trade shares are multiples of 100
every completed trade has entry and exit reason
```

Report total return, annual return, final equity, maximum drawdown, win rate, trade count, and the result path.

- [ ] **Step 5: Commit only source and tests if Task 3 changed them**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "test(backtest): audit minute execution constraints"
```

Do not stage or commit the generated result JSON or unrelated existing untracked files.
