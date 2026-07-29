# vn.py Single-Engine Opening-Volume Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the native opening-volume minute engine with vn.py while preserving the existing native page's configuration workflow and visual layout.

**Architecture:** `opening_volume_shared.py` remains the pure strategy and market-rule core. The vn.py service, strategy, and executor are its only adapter and execution path over standard date-partitioned Parquet; the frontend calls only `/vnpy/stream`.

**Tech Stack:** Python 3.11, FastAPI SSE, Polars, vn.py, React 18, TypeScript, Vite, pytest.

---

### Task 1: Complete vn.py configuration and executor behavior

**Files:**
- Modify: `backend/app/vnpy_backtest/service.py`
- Modify: `backend/app/vnpy_backtest/portfolio.py`
- Modify: `backend/app/vnpy_backtest/strategies/registry.py`
- Test: `backend/tests/vnpy_backtest/test_service.py`
- Test: `backend/tests/vnpy_backtest/test_portfolio_framework.py`

- [ ] **Step 1: Write failing tests for a single strategy and shared execution options**

```python
def test_registry_exposes_opening_volume_portfolio_only() -> None:
    assert [item.id for item in list_strategies()] == ["opening_volume_portfolio"]


def test_service_keeps_native_style_execution_options() -> None:
    result = VnpyMinuteBacktestService(repo).run(config)
    assert result["config"]["max_buy_volume_ratio"] == 1.0
    assert result["config"]["max_sell_volume_ratio"] == 0.5
    assert result["config"]["force_close_at_end"] is True
```

- [ ] **Step 2: Run and observe failure**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/vnpy_backtest/test_service.py tests/vnpy_backtest/test_portfolio_framework.py -q`

Expected: FAIL because legacy ids and missing fields remain.

- [ ] **Step 3: Add shared fields and matching semantics**

```python
max_buy_volume_ratio: float | None = 1.0
max_sell_volume_ratio: float | None = 1.0
force_close_at_end: bool = True
candidate_sort: str = "volume_ratio"
entry_fill: str = "next_minute_open"
exit_fill: str = "next_minute_open"
```

Use shared candidate sorting, current-equity cash reserve, both participation limits,
configured fills, same-day next-actual-bar expiry, force close, and common result
field names.

- [ ] **Step 4: Run focused vn.py regression and commit**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/vnpy_backtest/test_service.py tests/vnpy_backtest/test_portfolio_framework.py -q`

Expected: PASS.

Commit message: `feat(vnpy): complete opening volume portfolio options`.

### Task 2: Retain the native page style and submit only vn.py

**Files:**
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`
- Modify: `frontend/src/lib/backtestTask.ts`
- Modify: `backend/app/api/backtest.py`
- Test: `frontend/src/pages/backtest/openingVolumeSettings.test.ts`
- Test: `backend/tests/vnpy_backtest/test_api.py`

- [ ] **Step 1: Write failing source and API tests**

```typescript
assert.doesNotMatch(backtestSource, /engineMode|vnpyStrategyId|volumeLimitEnabled|minuteDataDir/)
assert.match(backtestSource, /max_buy_volume_ratio/)
assert.match(backtestSource, /max_sell_volume_ratio/)
```

```python
response = await backtest.vnpy_stream(
    request, strategy_id="opening_volume_portfolio",
    candidate_sort="score", force_close_at_end=False,
    max_buy_volume_ratio=1.0, max_sell_volume_ratio=0.5,
)
assert captured["config"].candidate_sort == "score"
```

- [ ] **Step 2: Run and observe failure**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/vnpy_backtest/test_api.py -q; cd ..\frontend; node --test src/pages/backtest/openingVolumeSettings.test.ts`

Expected: FAIL because the page still branches by engine.

- [ ] **Step 3: Remove only engine-specific controls**

Keep all native-style cards, filters, scoring, risk, range, fill, fee, capital, and
force-close controls. Remove the engine selector, vn.py strategy selector, fixed10%
checkbox, and external-directory input. Add `买入成交量上限` and `卖出成交量上限` percentage inputs; zero means unlimited.

- [ ] **Step 4: Expand `/vnpy/stream` request parsing**

```python
config = VnpyMinuteBacktestConfig(
    strategy_id="opening_volume_portfolio",
    candidate_sort=candidate_sort,
    force_close_at_end=force_close_at_end,
    max_buy_volume_ratio=max_buy_volume_ratio,
    max_sell_volume_ratio=max_sell_volume_ratio,
    params=strategy_params,
)
```

Return standard data-source and available-date information with vn.py no-data errors.

- [ ] **Step 5: Verify and commit**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/vnpy_backtest/test_api.py -q; cd ..\frontend; node --test src/pages/backtest/openingVolumeSettings.test.ts; corepack pnpm build`

Expected: PASS.

Commit message: `feat(backtest): use vnpy opening volume backend`.

### Task 3: Verify replacement and delete native implementation

**Files:**
- Delete: `backend/app/backtest/minute_portfolio.py`
- Delete: `backend/tests/backtest/test_minute_portfolio.py`
- Delete: `backend/tests/backtest/test_minute_portfolio_api.py`
- Modify: `backend/app/api/backtest.py`
- Modify: `docs/delivery-records/2026-07-29-vnpy-breakout-coexist.md`

- [ ] **Step 1: Write a failing static deletion test**

```python
def test_no_production_native_minute_portfolio_import_remains() -> None:
    assert "minute_portfolio" not in Path("app/api/backtest.py").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run it before deletion**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/vnpy_backtest/test_api.py -q`

Expected: FAIL until native references are removed.

- [ ] **Step 3: Delete native-only production and test files**

Remove the native stream route, native local-directory reader, native module, native
tests, and obsolete delivery claims. Do not delete matrix, daily, or unrelated
backtest functionality.

- [ ] **Step 4: Run final verification and commit**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/vnpy_backtest -q
cd ..\frontend
node --test src/pages/backtest/openingVolumeSettings.test.ts
corepack pnpm build
```

Expected: PASS.

Commit message: `refactor(backtest): remove native minute portfolio engine`.

## Plan Self-Review

- Task 1 makes vn.py a full replacement before deletion.
- Task 2 preserves the native page's visual workflow while removing engine choices.
- Task 3 keeps deletion test-gated and limited to native opening-volume code.
