# Opening-Volume Benchmark Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the A-only minute portfolio naturally reproduce the externally verified 2026-01-06 ten-symbol benchmark without symbol or date exceptions.

**Architecture:** Keep the existing local Parquet repository and minute-portfolio engine. Correct daily-bar construction at the repository boundary, then use the current bar's strict previous-high comparison in both portfolio and scanner paths. Preserve cumulative-volume, ranking, execution, sizing, and B/C branch behavior.

**Tech Stack:** Python 3.12, Polars, pytest, existing TickFlow minute-portfolio services.

---

### Task 1: Correct local daily aggregation

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py:314-323`

- [ ] **Step 1: Write the failing daily-aggregation test**

Add a test whose Parquet input contains misleading 09:15 and 09:24 rows plus valid 09:25 and 09:30 rows:

```python
def test_local_daily_aggregation_starts_with_0925_auction(tmp_path) -> None:
    symbol = "600000.SH"
    pl.DataFrame({
        "ts_code": ["600000.XSHG"] * 4,
        "trade_time": [
            "2026-01-05 09:15:00", "2026-01-05 09:24:00",
            "2026-01-05 09:25:00", "2026-01-05 09:30:00",
        ],
        "open": [9.0, 9.1, 10.0, 10.1],
        "high": [99.0, 98.0, 10.2, 10.3],
        "low": [1.0, 2.0, 9.8, 10.0],
        "close": [9.0, 9.1, 10.1, 10.2],
        "vol": [1_000.0, 2_000.0, 100.0, 200.0],
        "amount": [9_000.0, 18_200.0, 1_010.0, 2_040.0],
    }).write_parquet(tmp_path / f"{symbol}.parquet")

    daily = LocalMinuteParquetRepository(tmp_path).get_daily_batch(
        [symbol], date(2026, 1, 5), date(2026, 1, 5),
        ["symbol", "date", "open", "high", "low", "close", "volume"],
    ).row(0, named=True)

    assert daily == {
        "symbol": symbol, "date": date(2026, 1, 5),
        "open": 10.0, "high": 10.3, "low": 9.8,
        "close": 10.2, "volume": 300.0,
    }
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py::test_local_daily_aggregation_starts_with_0925_auction -q
```

Expected: FAIL because the current daily open/high/low/volume include 09:15-09:24.

- [ ] **Step 3: Apply the minimum repository fix**

Filter only the daily aggregation input, leaving minute rows available for cumulative-volume calculations:

```python
daily = (
    minutes
    .filter(pl.col("datetime").dt.time() >= time(9, 25))
    .sort(["symbol", "datetime"])
    .with_columns(pl.col("datetime").dt.date().alias("date"))
    .group_by(["symbol", "date"], maintain_order=True)
    .agg(
        pl.col("open").first(), pl.col("high").max(),
        pl.col("low").min(), pl.col("close").last(),
        pl.col("volume").sum(),
    )
    .sort(["symbol", "date"])
)
```

- [ ] **Step 4: Run the focused test and file regression**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py -q
```

Expected: all tests in the file pass.

- [ ] **Step 5: Commit the daily aggregation fix**

Stage only the two task files and commit:

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "fix(backtest): aggregate local daily bars from auction open"
```

### Task 2: Correct A-branch breakout and scanner time semantics

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/tests/test_opening_volume_strategy.py`
- Modify: `backend/app/backtest/minute_portfolio.py:570-599,687-729`

- [ ] **Step 1: Write failing portfolio tests for strict current-bar breakout**

Add a helper that supplies three bars and two tests:

```python
def _breakout_result(highs: tuple[float, float]):
    symbol = "600000.SH"
    day = date(2026, 1, 5)
    rows = [
        {"symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
         "open": 10.0, "high": highs[0], "low": 10.0, "close": 10.2,
         "volume": 100.0, "cumulative_volume": 100.0,
         "previous_cumulative_volume": 100.0},
        {"symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
         "open": 10.2, "high": highs[1], "low": 10.1, "close": 10.3,
         "volume": 200.0, "cumulative_volume": 300.0,
         "previous_cumulative_volume": 200.0},
        {"symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 32),
         "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
         "volume": 10_000.0, "cumulative_volume": 10_300.0,
         "previous_cumulative_volume": 300.0},
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": 10.0,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_ma5": 9.0,
    }}
    return MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1,
    )).run(rows, contexts)


def test_engine_breakout_does_not_require_first_cross_from_below() -> None:
    result = _breakout_result((10.6, 10.7))
    assert len(result["trades"]) == 1
    assert result["trades"][0]["entry_datetime"].endswith("09:32:00")


def test_engine_breakout_requires_strictly_greater_high() -> None:
    assert _breakout_result((10.4, 10.5))["trades"] == []
```

- [ ] **Step 2: Extend the scanner test with the benchmark time label**

Add this assertion to the existing scan-service test:

```python
assert result["rows"][0]["time"] == "09:31"
```

- [ ] **Step 3: Run the tests and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py::test_engine_breakout_does_not_require_first_cross_from_below backend/tests/backtest/test_minute_portfolio.py::test_engine_breakout_requires_strictly_greater_high backend/tests/test_opening_volume_strategy.py::test_opening_volume_scan_service_reads_tickflow_watchlist_only -q
```

Expected: the first-cross, equality, and scanner-time assertions fail under the old semantics.

- [ ] **Step 4: Apply the minimum engine and scanner fix**

In both signal paths, replace the first-cross expression with:

```python
crossed_previous_high=float(bar["high"]) > float(context["previous_high"])
```

Remove the now-unused `intraday_high` state. In the scanner result, use:

```python
"time": (timestamp + timedelta(minutes=1)).strftime("%H:%M"),
```

- [ ] **Step 5: Run focused and related regressions**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py backend/tests/test_opening_volume_strategy.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the signal-semantics fix**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py backend/tests/test_opening_volume_strategy.py
git diff --cached --check
git commit -m "fix(strategy): align opening-volume breakout semantics"
```

### Task 3: Verify the real 609-symbol benchmark and push the feature branch

**Files:**
- No production-file changes expected.

- [ ] **Step 1: Run the 2026-01-06 external-data regression**

Use the supplied pool and local data directory to run A-only with the agreed
cost, sizing, reserve, and volume constraints. Assert the unique entry symbols
and entry-time distribution are exactly the benchmark set and `8+1+1`.

Run from the repository root:

```powershell
$env:PYTHONPATH='backend'
@'
import ast
from collections import Counter
from datetime import date
from pathlib import Path

import polars as pl

from app.backtest.minute_portfolio import (
    MinutePortfolioConfig,
    MinutePortfolioService,
    OpeningVolumeStrategyParams,
)

POOL = Path(r"C:\Users\Administrator\.codex\attachments\34532355-938b-4efc-96be-e292b4af57d1\pasted-text.txt")
EXPECTED = {
    "000737.SZ", "000949.SZ", "002353.SZ", "002851.SZ", "002978.SZ",
    "301219.SZ", "600711.SH", "603799.SH", "688127.SH", "688556.SH",
}

class HostRepo:
    def get_index_daily(self, *args, **kwargs):
        return pl.DataFrame()

    def get_name_map(self, symbols):
        return {}

symbols = ast.literal_eval(POOL.read_text(encoding="utf-8"))
result = MinutePortfolioService(HostRepo()).run(MinutePortfolioConfig(
    symbols=symbols,
    start=date(2026, 1, 6), end=date(2026, 1, 6),
    minute_data_dir=r"F:\quant\data\minute_1min_pytdx",
    initial_capital=10_000_000.0, max_positions=10,
    commission_pct=0.00012, stamp_tax_pct=0.0005,
    slippage_bps=1.0, cash_reserve_ratio=0.03,
    max_buy_volume_ratio=1.0,
    strategy_params=OpeningVolumeStrategyParams(
        enable_branch_a=True, enable_branch_b=False, enable_branch_c=False,
        branch_a_volume_multiple=1.5,
    ),
))
entries = {
    trade["symbol"]: trade["entry_datetime"][11:16]
    for trade in result["trades"]
    if trade["entry_date"] == "2026-01-06"
}
assert set(entries) == EXPECTED, entries
assert Counter(entries.values()) == Counter({"09:31": 8, "09:32": 1, "09:33": 1}), entries
print(sorted(entries.items()))
'@ | backend\.venv\Scripts\python.exe -
```
```

Expected symbols:

```text
000737.SZ 000949.SZ 002353.SZ 002851.SZ 002978.SZ
301219.SZ 600711.SH 603799.SH 688127.SH 688556.SH
```

Expected entry times: eight at 09:31, one at 09:32, and one at 09:33.

- [ ] **Step 2: Run the full related test set**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py backend/tests/test_opening_volume_strategy.py backend/tests/backtest/test_minute_portfolio_api.py -q
```

Expected: all tests pass with zero failures.

- [ ] **Step 3: Inspect the branch-only delivery diff**

Run:

```powershell
git status --short
git diff vnpy-origin/main...HEAD --check
git log --oneline vnpy-origin/main..HEAD
```

Verify that unrelated local files remain untracked and absent from commits.

- [ ] **Step 4: Push only the feature branch**

Run:

```powershell
git push -u vnpy-origin codex/minute-opening-volume-portfolio
git status -sb
```

Do not switch to, merge into, or push `main`.
