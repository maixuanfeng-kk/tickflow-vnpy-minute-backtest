# Dynamic Portfolio Cash Reserve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `cash_reserve_ratio` reserve a percentage of current marked-to-market portfolio equity and allocate the remaining equity evenly across the configured maximum number of positions.

**Architecture:** Keep the existing `MinutePortfolioConfig -> MinutePortfolioEngine` flow. At the pending-buy fill point, mark existing positions with the current bar open (falling back to latest close and entry price), derive the current-equity cash floor and target position value, and then retain the existing commission, minute-volume, and board-lot constraints.

**Tech Stack:** Python 3.11+, dataclasses, pytest, existing deterministic minute-portfolio simulator.

---

## File Map

- Modify `backend/app/backtest/minute_portfolio.py`: calculate current equity, dynamic reserve cash, and dynamic per-position target immediately before each buy fill.
- Modify `backend/tests/backtest/test_minute_portfolio.py`: cover a fully allocated ten-position portfolio, current-equity repricing, volume-limit compatibility, and the zero-reserve default.

### Task 1: Specify Portfolio-Level Reserve Behavior

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`

- [ ] **Step 1: Add a ten-position failing test**

Add a helper that generates ten A-branch signals at 09:30 and fills them at 09:31 with price `1.0`, zero costs, and sufficient minute volume. Configure `initial_capital=100_000`, `max_positions=10`, and `cash_reserve_ratio=0.03`.

```python
def test_engine_limits_total_position_targets_to_97_percent_of_equity() -> None:
    result = _simultaneous_entry_result(
        symbol_count=10,
        initial_capital=100_000.0,
        max_positions=10,
        cash_reserve_ratio=0.03,
    )

    assert len(result["trades"]) == 10
    assert {trade["shares"] for trade in result["trades"]} == {9_700}
    assert sum(trade["entry_cost"] for trade in result["trades"]) == 97_000.0
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py::test_engine_limits_total_position_targets_to_97_percent_of_equity -q
```

Expected: FAIL because the current engine targets 10% of initial capital for the early fills and applies 3% only to remaining cash.

- [ ] **Step 3: Add a current-equity repricing failing test**

Create a two-symbol, two-day case. On day one, buy only 1,000 shares of the first symbol at `10.0` because of the minute-volume cap. On day two, mark that position at `20.0`, signal the second symbol, and fill it at `10.0`. Current equity is then `110,000`, so the second position target is `53,350`; after board-lot rounding it must buy 5,300 shares.

```python
def test_engine_position_target_follows_current_marked_equity() -> None:
    result = _two_day_mark_to_market_sizing_result()
    second_trade = next(trade for trade in result["trades"] if trade["symbol"] == "000002.SZ")

    assert second_trade["shares"] == 5_300
    assert second_trade["entry_cost"] == 53_000.0
```

- [ ] **Step 4: Run both tests and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py -k "97_percent_of_equity or current_marked_equity" -q
```

Expected: both tests FAIL because the engine uses `initial_capital / max_positions` and `cash * 0.97`.

- [ ] **Step 5: Commit the isolated test specification**

```powershell
git add backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "test(backtest): specify portfolio cash reserve"
```

### Task 2: Implement Dynamic Equity Reserve and Target

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py:477-497`
- Test: `backend/tests/backtest/test_minute_portfolio.py`

- [ ] **Step 1: Calculate marked-to-market equity before each fill**

Replace the fixed target and remaining-cash percentage with the following calculation at the existing pending-buy fill point:

```python
current_equity = cash
for held_symbol, position in positions.items():
    held_bar = bars.get(held_symbol)
    mark_price = float(held_bar.get("open") or held_bar.get("close") or 0) if held_bar else 0.0
    if mark_price <= 0:
        mark_price = float(latest_closes.get(held_symbol) or position["entry_price"])
    current_equity += position["shares"] * mark_price

reserve_cash = current_equity * self.config.cash_reserve_ratio
target = current_equity * (1 - self.config.cash_reserve_ratio) / self.config.max_positions
spendable_cash = max(cash - reserve_cash, 0.0)
```

Keep the existing all-in commission denominator, minute-volume cap, board-lot rounding, and insufficient-size checks unchanged.

- [ ] **Step 2: Run the two new tests and verify GREEN**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py -k "97_percent_of_equity or current_marked_equity" -q
```

Expected: both tests PASS.

- [ ] **Step 3: Verify existing sizing compatibility tests**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py -k "buy_sizing or reserves_three_percent or buy_volume or stricter_cash" -q
```

Expected: all selected tests PASS. The single-position 3% case remains 9,700 shares, the execution-volume cap still wins at 500 shares, and the default zero-reserve case remains 10,000 shares.

- [ ] **Step 4: Run the complete related regression set**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py backend/tests/test_opening_volume_strategy.py -q
```

Expected: all tests PASS.

- [ ] **Step 5: Inspect and commit only source and tests**

```powershell
git status --short
git diff --check
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git diff --cached
git commit -m "fix(backtest): reserve cash from current portfolio equity"
```

- [ ] **Step 6: Verify the committed result**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py backend/tests/test_opening_volume_strategy.py -q
git show --stat --oneline HEAD
git status --short
```

Expected: the related regression set passes; the commit contains only the engine and minute-portfolio test files; generated `.tmp` results and the five pre-existing untracked files remain uncommitted.
