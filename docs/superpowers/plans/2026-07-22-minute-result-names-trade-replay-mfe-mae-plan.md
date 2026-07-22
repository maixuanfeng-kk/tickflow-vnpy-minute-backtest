# Minute Result Names, Trade Replay, and MFE/MAE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return stock names and valid trade dates for minute-portfolio results, prevent invalid-date crashes in trade replay, and replace per-symbol best/worst returns with holding-period maximum floating gain and maximum floating loss.

**Architecture:** Keep the calculation inside `MinutePortfolioEngine`, where every held minute bar is already available. Enrich completed trades in `MinutePortfolioService` using the main repository's existing name map, then make the shared frontend explicitly detect minute-portfolio results for metric labels while using a small pure date helper for safe replay ranges.

**Tech Stack:** Python 3, Polars, NumPy, pytest, TypeScript, React 18, Node 24 native test runner, Vite.

---

### Task 1: Record Standard Dates and Holding Excursions

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`

- [ ] **Step 1: Write failing engine assertions**

Extend the existing engine test so a completed trade must expose standard dates and excursion fields derived from held minute highs/lows:

```python
trade = result["trades"][0]
unit_cost = trade["entry_cost"] / trade["shares"]
assert trade["entry_date"] == "2026-01-05"
assert trade["exit_date"] == "2026-01-06"
assert trade["max_floating_gain_pct"] == pytest.approx(max(0.0, 11.0 / unit_cost - 1), abs=1e-6)
assert trade["max_floating_loss_pct"] == pytest.approx(min(0.0, 10.2 / unit_cost - 1), abs=1e-6)
```

Add a focused boundary test whose exit-minute high and low are extreme and assert those prices do not affect MFE/MAE after a pending sell executes at that minute's open.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
$env:PYTHONPATH = 'D:\quant\tickflow-stock-panel\backend'
& 'D:\quant\tickflow-stock-panel\.worktrees\minute-opening-volume-portfolio\backend\.venv\Scripts\python.exe' -m pytest backend\tests\backtest\test_minute_portfolio.py -q
```

Expected: failure because `entry_date`, `exit_date`, `max_floating_gain_pct`, and `max_floating_loss_pct` are absent.

- [ ] **Step 3: Implement the minimal engine state**

Initialize both excursion percentages at zero when a buy executes. After pending sells and buys are processed, update each still-open position from that symbol's current minute high and low:

```python
unit_cost = position["entry_cost"] / position["shares"]
high_return = float(bar["high"]) / unit_cost - 1.0
low_return = float(bar["low"]) / unit_cost - 1.0
position["max_floating_gain_pct"] = max(position["max_floating_gain_pct"], high_return)
position["max_floating_loss_pct"] = min(position["max_floating_loss_pct"], low_return)
```

Write ISO `entry_date`/`exit_date` and rounded excursion values into normal and forced-liquidation trade dictionaries. Do not change realized PnL, entry/exit rules, or fees.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the command from Step 2. Expected: all `test_minute_portfolio.py` tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "feat(backtest): track minute trade excursions"
```

### Task 2: Enrich Result Names and Aggregate Excursions

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`

- [ ] **Step 1: Write failing service tests**

Give the test repository a name map:

```python
def get_name_map(self, symbols):
    return {"600000.SH": "浦发银行"}
```

Assert the completed result contract:

```python
assert result["trades"][0]["name"] == "浦发银行"
assert result["per_symbol_stats"][0]["name"] == "浦发银行"
assert result["per_symbol_stats"][0]["best"] == result["trades"][0]["max_floating_gain_pct"]
assert result["per_symbol_stats"][0]["worst"] == result["trades"][0]["max_floating_loss_pct"]
```

Add a direct multi-trade aggregation test proving `best` uses the largest MFE and `worst` uses the smallest MAE while total return and win rate still use realized `pnl_pct`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run the Task 1 pytest command. Expected: failure because names are absent and `_per_symbol` still aggregates realized final returns for best/worst.

- [ ] **Step 3: Implement service enrichment and aggregation**

Use `self.repo.get_name_map(config.symbols)` when available, with an empty-map fallback for compatible repositories. Attach names to trades before building per-symbol statistics. Change `_per_symbol` to aggregate:

```python
"best": round(max(float(t.get("max_floating_gain_pct", 0.0)) for t in symbol_trades), 6),
"worst": round(min(float(t.get("max_floating_loss_pct", 0.0)) for t in symbol_trades), 6),
```

Preserve compound total return and realized-trade win rate.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Task 1 pytest command. Expected: all focused backend tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "fix(backtest): enrich minute result metadata"
```

### Task 3: Make Trade Replay Date Parsing Safe

**Files:**
- Create: `frontend/src/pages/backtest/components/tradeDates.ts`
- Create: `frontend/src/pages/backtest/components/tradeDates.test.ts`
- Modify: `frontend/src/pages/backtest/components/TradeKlineModal.tsx`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Write failing TypeScript tests**

Create native Node tests for a pure helper API:

```typescript
import test from 'node:test'
import assert from 'node:assert/strict'
import { addCalendarDays, resolveTradeDates } from './tradeDates.ts'

test('resolves dates from minute datetimes', () => {
  assert.deepEqual(resolveTradeDates({
    entry_datetime: '2026-01-05 09:31:00',
    exit_datetime: '2026-01-06 09:30:00',
  }), { entry: '2026-01-05', exit: '2026-01-06' })
})

test('rejects invalid trade dates without throwing', () => {
  assert.equal(resolveTradeDates({ entry_date: 'undefined', exit_date: '' }), null)
  assert.equal(addCalendarDays('undefined', -45), null)
})
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
node --test frontend/src/pages/backtest/components/tradeDates.test.ts
```

Expected: failure because `tradeDates.ts` does not exist.

- [ ] **Step 3: Implement and integrate the date helper**

Implement strict ISO calendar-date validation, UTC calendar addition, and fallback from `entry_date`/`exit_date` to `entry_datetime`/`exit_datetime`. Update `StrategyBacktestTrade` with optional datetime fields. Refactor `TradeKlineModal` to use the resolved dates and render a compact “缺少有效交易日期” state instead of calling `toISOString()` for invalid input.

- [ ] **Step 4: Run tests and TypeScript build**

Run:

```powershell
node --test frontend/src/pages/backtest/components/tradeDates.test.ts
npx -y pnpm@9.10.0 --dir frontend build
```

Expected: native tests pass and the production build exits 0.

- [ ] **Step 5: Commit Task 3**

```powershell
git add frontend/src/lib/api.ts frontend/src/pages/backtest/components/tradeDates.ts frontend/src/pages/backtest/components/tradeDates.test.ts frontend/src/pages/backtest/components/TradeKlineModal.tsx
git diff --cached --check
git commit -m "fix(backtest): validate trade replay dates"
```

### Task 4: Display Names and Explicit Minute Excursion Labels

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`

- [ ] **Step 1: Establish the frontend contract**

Extend per-symbol result types with optional `name`, `max_floating_gain_pct`, and `max_floating_loss_pct`. Keep `best`/`worst` because other engines use them.

- [ ] **Step 2: Implement minute-specific labels and values**

Detect `result.config.engine === 'minute_portfolio'`. For that engine only, show column labels “最高浮盈” and “最大浮亏”; otherwise retain “最佳” and “最差”. Prefer `r.name` when rendering the symbol and use `priceColorClass()` for both metric cells instead of fixed column colors.

- [ ] **Step 3: Run frontend verification**

Run:

```powershell
npx -y pnpm@9.10.0 --dir frontend build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 4: Commit Task 4**

```powershell
git add frontend/src/lib/api.ts frontend/src/pages/backtest/StrategyBacktest.tsx
git diff --cached --check
git commit -m "fix(backtest): label minute excursion metrics"
```

### Task 5: Regression Verification

**Files:**
- Verify only; no expected production edits.

- [ ] **Step 1: Run backend regression tests**

```powershell
$env:PYTHONPATH = 'D:\quant\tickflow-stock-panel\backend'
& 'D:\quant\tickflow-stock-panel\.worktrees\minute-opening-volume-portfolio\backend\.venv\Scripts\python.exe' -m pytest backend\tests\backtest\test_minute_portfolio.py backend\tests\backtest\test_minute_portfolio_api.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend tests and build**

```powershell
node --test frontend/src/pages/backtest/components/tradeDates.test.ts
npx -y pnpm@9.10.0 --dir frontend build
```

Expected: tests and build pass.

- [ ] **Step 3: Check the scoped Git diff**

```powershell
git diff --check
git status --short
git log --oneline -8
```

Expected: no whitespace errors; only pre-existing unrelated untracked files remain outside the scoped planning directory; no sync or push has occurred.

