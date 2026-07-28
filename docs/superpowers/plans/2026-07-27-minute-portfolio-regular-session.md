# Minute Portfolio Regular-Session Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent pre-open minute bars from triggering or executing trades while retaining their contribution to daily and cumulative-volume context.

**Architecture:** Keep `_load_rows_and_context` unchanged so 09:25 auction data remains in daily aggregation and cumulative volume. At the `MinutePortfolioService.run` boundary, trim executable rows to the requested date range and `datetime.time() >= 09:30` before passing them to `MinutePortfolioEngine`.

**Tech Stack:** Python 3.12, Polars, pytest

---

### Task 1: Enforce the regular-session execution boundary

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py:876-880`

- [x] **Step 1: Write the failing regression test**

Add this test after `test_service_excludes_warmup_minutes_from_execution`:

```python
def test_service_ignores_preopen_bars_when_triggering_exits() -> None:
    symbol = "600000.SH"

    class Repo:
        def get_daily_batch(self, symbols, start, end, columns):
            return pl.DataFrame({
                "symbol": [symbol, symbol],
                "date": [date(2026, 1, 2), date(2026, 1, 5)],
                "open": [11.0, 10.0],
                "high": [10.5, 10.6],
                "close": [10.0, 10.0],
                "ma5": [9.0, 9.0],
            })

        def get_minute_range(self, symbols, start, end, asset_type):
            return pl.DataFrame({
                "symbol": [symbol] * 7,
                "datetime": [
                    datetime(2026, 1, 2, 9, 30),
                    datetime(2026, 1, 5, 9, 30),
                    datetime(2026, 1, 5, 9, 31),
                    datetime(2026, 1, 6, 9, 16),
                    datetime(2026, 1, 6, 9, 17),
                    datetime(2026, 1, 6, 9, 30),
                    datetime(2026, 1, 6, 9, 31),
                ],
                "open": [10.0, 10.0, 10.0, 9.0, 9.0, 9.0, 8.8],
                "high": [10.0, 10.6, 10.1, 9.1, 9.1, 9.1, 8.9],
                "low": [10.0, 10.0, 9.9, 8.9, 8.9, 8.9, 8.7],
                "close": [10.0, 10.2, 10.0, 9.0, 9.0, 9.0, 8.8],
                "volume": [100.0, 150.0, 1_000.0, 100.0, 100.0, 100.0, 1_000.0],
                "amount": [1_000.0] * 7,
            })

        def get_index_daily(self, symbol, start, end, columns):
            return pl.DataFrame()

    result = MinutePortfolioService(Repo()).run(MinutePortfolioConfig(
        symbols=[symbol],
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        initial_capital=100_000.0,
        max_positions=1,
        commission_pct=0.0,
        stamp_tax_pct=0.0,
        slippage_bps=0.0,
    ))

    assert result["trades"][0]["exit_reason"] == "stop_loss"
    assert result["trades"][0]["exit_datetime"] == "2026-01-06 09:31:00"
```

- [x] **Step 2: Run the regression test and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py::test_service_ignores_preopen_bars_when_triggering_exits -q
```

Expected: FAIL because the current engine exits at `2026-01-06 09:17:00`.

- [x] **Step 3: Apply the minimal service-boundary filter**

Change the existing `raw_rows` date filter in `MinutePortfolioService.run` to:

```python
        raw_rows = [
            row for row in raw_rows
            if config.start <= row["datetime"].date() <= config.end
            and row["datetime"].time() >= time(9, 30)
        ]
```

Do not change `_load_rows_and_context`, `LocalMinuteParquetRepository.get_daily_batch`, or strategy scan parameters.

- [x] **Step 4: Run the regression test and verify GREEN**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py::test_service_ignores_preopen_bars_when_triggering_exits -q
```

Expected: `1 passed`.

- [x] **Step 5: Run focused and full backend regression tests**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py -q
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
git diff --check
```

Expected: all tests pass and `git diff --check` produces no output.

Execution note: the unfiltered backend suite cannot collect three `backend/tests/vnpy_backtest` modules because the current environment does not install the optional `vnpy` package. Verify the available backend suite with `--ignore=backend/tests/vnpy_backtest` and report the excluded dependency explicitly.

Real-data verification note: the 609-symbol rerun completed, but the analysis script later failed to overwrite `.tmp/backtests/external-report-orders.csv` because another process held the file open. The raw JSON is written before that CSV and was updated successfully; validate the new raw result directly instead of repeating the expensive calculation.

- [x] **Step 6: Commit only the implementation and regression test**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py docs/superpowers/plans/2026-07-27-minute-portfolio-regular-session.md
git diff --cached --check
git diff --cached
git commit -m "fix(backtest): ignore preopen bars during execution"
```

Expected: the commit contains only the service filter, regression test, and this implementation plan.
