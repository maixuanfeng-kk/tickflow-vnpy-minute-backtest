# Minute Portfolio Results and Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return complete portfolio statistics and curves for the opening-volume backtest, while streaming real file-loading and trading-day progress to the existing UI.

**Architecture:** Extend the native minute portfolio engine to record net trade PnL and daily mark-to-market equity. The service derives the existing strategy-result contract from those records and adapts repository/index data for the benchmark. Progress flows from the Parquet reader and engine through one callback into the existing SSE `progress` events; the frontend keeps the same generic progress component and persists the local data directory.

**Tech Stack:** Python 3, Polars, NumPy, FastAPI SSE, React/TypeScript, pytest, Vite.

---

### Task 1: Net trade PnL and daily portfolio equity

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`

- [ ] **Step 1: Write failing engine assertions**

Add a test that runs the existing two-day fixture and asserts the wished-for result contract:

```python
assert result["trades"][0]["pnl_amount"] == pytest.approx(
    result["trades"][0]["pnl_pct"] * result["trades"][0]["entry_cost"],
    abs=0.1,
)
assert result["trades"][0]["duration"] == 1
assert [row["date"] for row in result["equity_curve"]] == ["2026-01-05", "2026-01-06"]
assert result["equity_curve"][-1]["value"] == pytest.approx(result["cash"], abs=0.01)
assert result["drawdown_curve"][-1]["value"] <= 0
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
& 'D:\quant\tickflow-stock-panel\.worktrees\minute-opening-volume-portfolio\backend\.venv\Scripts\python.exe' -m pytest tests\backtest\test_minute_portfolio.py -q
```

Expected: FAIL because trades lack `pnl_amount`, `pnl_pct`, `entry_cost`, and the engine lacks equity/drawdown curves.

- [ ] **Step 3: Implement net proceeds and daily snapshots**

Store `entry_cost` in each position. On every exit calculate:

```python
net_proceeds = shares * exit_price * (1 - commission_pct - stamp_tax_pct)
pnl_amount = net_proceeds - position["entry_cost"]
pnl_pct = pnl_amount / position["entry_cost"] if position["entry_cost"] else 0.0
duration = (exit_timestamp.date() - position["entry_date"]).days
```

At each trading-day boundary append mark-to-market equity using cash plus open positions valued at their latest close. Return `cash`, `trades`, `equity_curve`, and `drawdown_curve`.

- [ ] **Step 4: Verify GREEN and commit**

Run the Task 1 test command. Expected: all minute portfolio engine tests pass.

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "feat(backtest): record minute portfolio equity and pnl"
```

### Task 2: Complete result statistics, benchmark, and per-symbol output

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`

- [ ] **Step 1: Write failing service contract test**

Extend the service fixture with `get_index_daily` and assert:

```python
expected_stats = {
    "total_return", "annual_return", "sharpe", "sortino", "max_drawdown",
    "mc_maxdd_p50", "mc_maxdd_p95", "win_rate", "n_trades", "final_equity",
    "total_trade_count", "end_balance",
}
assert expected_stats <= result["stats"].keys()
assert result["equity_curve"]
assert result["drawdown_curve"]
assert result["benchmark_curve"] == [
    {"date": "2026-01-05", "close": 100.0},
    {"date": "2026-01-06", "close": 101.0},
]
assert result["per_symbol_stats"][0]["symbol"] == "600000.SH"
```

- [ ] **Step 2: Verify RED**

Run the Task 1 test command. Expected: FAIL because the service only returns two legacy stats and empty arrays.

- [ ] **Step 3: Implement statistics helpers**

Use actual final equity for total return, actual daily equity changes for Sharpe/Sortino and drawdown, and net trade `pnl_pct` for win rate and deterministic Monte Carlo. Return zero-valued fields for no-trade runs. Read benchmark rows through `self.repo.get_index_daily("000001.XSHG", start, end, columns=["date", "close"])`; catch repository errors and return an empty benchmark only.

- [ ] **Step 4: Verify GREEN and commit**

Run the Task 1 test command. Expected: all service and engine tests pass.

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "feat(backtest): complete minute portfolio statistics"
```

### Task 3: Stream real progress from Parquet loading and trading days

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/tests/backtest/test_minute_portfolio_api.py`
- Modify: `backend/app/backtest/minute_portfolio.py`
- Modify: `backend/app/api/backtest.py`

- [ ] **Step 1: Write failing reader and SSE tests**

Use two temporary Parquet files and collect service progress:

```python
messages: list[dict] = []
service.run(config, progress_callback=messages.append)
days = [message["day"] for message in messages]
assert days == sorted(days)
assert any("读取分钟数据" in message["date"] for message in messages)
assert any("撮合交易日" in message["date"] for message in messages)
assert messages[-1]["day"] == messages[-1]["total"] == 1000
```

In the API fake service call `progress_callback` twice and assert the SSE body contains both progress payloads before `event: done`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
& 'D:\quant\tickflow-stock-panel\.worktrees\minute-opening-volume-portfolio\backend\.venv\Scripts\python.exe' -m pytest tests\backtest\test_minute_portfolio.py tests\backtest\test_minute_portfolio_api.py -q
```

Expected: FAIL because `MinutePortfolioService.run` does not accept a progress callback.

- [ ] **Step 3: Implement progress callbacks**

Add `progress_callback: Callable[[dict], None] | None = None` to the service. The local reader reports every five processed symbol files and at phase end. Map loading to 0–650, engine trading dates to 650–950, and result preparation to 950–1000. Update the API worker to call:

```python
result = MinutePortfolioService(repo).run(config, progress_callback=job.progress.append)
```

Keep the current SSE keep-alive comment events.

- [ ] **Step 4: Verify GREEN and commit**

Run the Task 3 test command. Expected: all tests pass with multiple monotonic progress events.

```powershell
git add backend/app/backtest/minute_portfolio.py backend/app/api/backtest.py backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py
git diff --cached --check
git commit -m "feat(backtest): stream minute portfolio progress"
```

### Task 4: Persist the new default local minute directory

**Files:**
- Modify: `frontend/src/lib/storage.ts`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`

- [ ] **Step 1: Extend stored configuration**

Add `minuteDataDir?: string` to `storage.strategyBacktestLast`. Initialize state with:

```typescript
const [minuteDataDir, setMinuteDataDir] = useState(
  saved?.minuteDataDir ?? 'F:\\quant\\data\\minute_1min_pytdx',
)
```

Save `minuteDataDir` with the existing successful backtest configuration.

- [ ] **Step 2: Build and commit**

Run:

```powershell
npx -y pnpm@9.10.0 --dir frontend build
```

Expected: TypeScript and Vite build exit 0.

```powershell
git add frontend/src/lib/storage.ts frontend/src/pages/backtest/StrategyBacktest.tsx
git diff --cached --check
git commit -m "feat(backtest): persist minute parquet directory"
```

### Task 5: Full verification and UI smoke test

**Files:**
- Modify only files listed above if verification reveals a scoped defect.

- [ ] **Step 1: Run backend regression**

```powershell
& 'D:\quant\tickflow-stock-panel\.worktrees\minute-opening-volume-portfolio\backend\.venv\Scripts\python.exe' -m pytest tests\backtest\test_minute_portfolio.py tests\backtest\test_minute_portfolio_api.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run frontend build and repository checks**

```powershell
npx -y pnpm@9.10.0 --dir frontend build
git diff --check
git status --short
```

Expected: build exits 0, no whitespace errors, and only the user's pre-existing unrelated untracked files remain.

- [ ] **Step 3: Run the real UI/API backtest**

Use `F:\quant\data\minute_1min_pytdx`, the 609-symbol watchlist, and `2026-01-01` through `2026-02-01`. Confirm several increasing progress events, `day=total=1000`, populated statistics, non-empty equity/drawdown curves, and a completed result.

## Plan self-review

- Spec coverage: Tasks 1–4 cover equity, PnL, statistics, benchmark, Monte Carlo, per-symbol output, real progress, errors, and directory persistence.
- Type consistency: progress remains `day/total/date/equity`; service and API use `progress_callback=job.progress.append`.
- Scope: no strategy entry/exit or other backtest engine behavior changes.
