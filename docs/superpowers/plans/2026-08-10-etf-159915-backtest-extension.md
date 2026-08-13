# 159915 ETF Backtest Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add strategy-specific rules, local data readiness, run gating, and signal-to-fill tracing to the existing backtest workspace without changing other strategies.

**Architecture:** Keep `StrategyBacktest` as the generic shell and mount a focused 159915 extension keyed by `strategy_id`. A new backend readiness service compares ETF daily and minute Parquet coverage; the API exposes it and rechecks it before starting an ETF job. Pure frontend helpers own extension lookup, gate state, and signal/fill joining, while React components render the approved split layout.

**Tech Stack:** FastAPI, Polars, pytest, React 18, TypeScript, TanStack Query, Tailwind CSS, lucide-react, Node test runner.

---

### Task 1: ETF Readiness Service

**Files:**
- Create: `backend/app/vnpy_backtest/readiness.py`
- Create: `backend/tests/vnpy_backtest/test_etf_159915_readiness.py`

- [ ] **Step 1: Write failing readiness tests**

Create fixtures that write `kline_etf_daily/date=2025-01-02/part.parquet` and `kline_etf_minute/date=2025-01-02/part.parquet`. Assert:

```python
result = Etf159915ReadinessService(tmp_path).check(
    symbol="159915.SZ",
    start=date(2025, 1, 2),
    end=date(2025, 1, 3),
)
assert result["ready"] is True
assert result["blocking_reasons"] == []
assert result["coverage"]["daily"]["warmup_days"] == 10
assert result["coverage"]["minute"]["trading_days"] == 2
```

Add separate tests for insufficient warmup, a whole missing minute day, missing `09:30`/`15:00`, missing `14:59`, sparse row counts, zero-volume rows, and absent dataset directories.

- [ ] **Step 2: Run the tests and confirm RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\vnpy_backtest\test_etf_159915_readiness.py -q
```

Expected: collection fails because `app.vnpy_backtest.readiness` does not exist.

- [ ] **Step 3: Implement the readiness service**

Create `Etf159915ReadinessService` with a single public method:

```python
class Etf159915ReadinessService:
    EXPECTED_MINUTE_ROWS = 241
    REQUIRED_WARMUP_DAYS = 10

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def check(self, *, symbol: str, start: date, end: date) -> dict[str, object]:
        return self._check_symbol_range(symbol=symbol, start=start, end=end)
```

Use lazy Parquet scans filtered to `symbol`; derive expected trading days from ETF daily rows in `[start, end]`; compare against symbol minute rows. Return bounded missing/sparse samples, counts, first/last dates, `blocking_reasons`, `warnings`, and `ready = not blocking_reasons`. Treat missing dataset/schema/read errors as blocking payloads rather than uncaught exceptions.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run the same pytest command. Expected: all readiness tests pass.

### Task 2: Readiness API and Server-Side Gate

**Files:**
- Modify: `backend/app/api/backtest.py`
- Modify: `backend/tests/vnpy_backtest/test_api.py`

- [ ] **Step 1: Add failing API tests**

Extend the route assertion with:

```python
("/api/backtest/vnpy/readiness", frozenset({"GET"}))
```

Add tests that monkeypatch `Etf159915ReadinessService.check` and verify:

```python
response = backtest.vnpy_readiness(
    request,
    strategy_id="etf_159915_minute",
    symbols="159915.SZ",
    start="2025-01-01",
    end="2025-12-31",
)
assert response["strategy_id"] == "etf_159915_minute"
```

Add an async stream test where readiness returns `ready=False` and assert `HTTPException` is raised before a worker starts.

- [ ] **Step 2: Run API tests and confirm RED**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\vnpy_backtest\test_api.py -q
```

Expected: route/function assertions fail.

- [ ] **Step 3: Implement endpoint and shared validation**

Add a private request parser shared by readiness and stream for registered strategy, normalized symbols, and dates. Add:

```python
@router.get("/vnpy/readiness")
def vnpy_readiness(request: Request, strategy_id: str, symbols: str, start: str, end: str) -> dict:
    spec, normalized_symbols, start_date, end_date = _parse_vnpy_scope(strategy_id, symbols, start, end)
    return _readiness_for_scope(request, spec.id, normalized_symbols, start_date, end_date)
```

For `etf_159915_minute`, instantiate the readiness service with `request.app.state.repo.store.data_dir`. For other strategies, return a generic ready payload without scanning ETF data. In `vnpy_stream`, repeat the ETF check and raise HTTP 400 with joined blocking reasons when `ready` is false.

- [ ] **Step 4: Run API and ETF backend tests**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\vnpy_backtest\test_api.py backend\tests\vnpy_backtest\test_etf_159915_data_flow.py backend\tests\vnpy_backtest\test_etf_159915_execution.py backend\tests\vnpy_backtest\test_etf_159915_strategy.py -q
```

Expected: all tests pass.

### Task 3: Pure Frontend Extension Helpers

**Files:**
- Create: `frontend/src/pages/backtest/strategy-extensions/etf159915.ts`
- Create: `frontend/tests/etf-159915-backtest-extension.test.mjs`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Write failing Node tests**

Test exact extension resolution, gate behavior, rule IDs, and fill joining:

```javascript
assert.equal(resolveBacktestExtension('etf_159915_minute')?.id, 'etf_159915_minute')
assert.equal(resolveBacktestExtension('opening_breakout_pool'), null)
assert.equal(isEtf159915RunBlocked({ isLoading: true }), true)
assert.equal(isEtf159915RunBlocked({ data: { ready: true } }), false)
assert.equal(buildEtf159915ExecutionRows(signals, fills)[0].fillPrice, 1.9548045)
```

- [ ] **Step 2: Run the test and confirm RED**

```powershell
node --test frontend\tests\etf-159915-backtest-extension.test.mjs
```

Expected: module import fails.

- [ ] **Step 3: Implement pure helpers and API types**

Export:

```typescript
export const ETF_159915_EXTENSION = { id, symbol, ruleVersion, fixedSettings, entryRules, exitRules }
export const resolveBacktestExtension = (strategyId: string) =>
  strategyId === ETF_159915_EXTENSION.id ? ETF_159915_EXTENSION : null
export const isEtf159915RunBlocked = (state: ReadinessQueryState) =>
  state.isLoading || state.isError || !state.data?.ready
export const buildEtf159915ExecutionRows = (signals, fills) => {
  const fillsBySignal = new Map(fills.map(fill => [fill.signal_id, fill]))
  return signals.map(signal => ({ signal, fill: fillsBySignal.get(signal.id) ?? null }))
}
```

In `api.ts`, add `Etf159915Readiness`, add optional `fills?: StrategyBacktestTrade[]` to `StrategyBacktestResult`, and add `api.vnpyReadiness(strategyId, symbols, start, end)`.

- [ ] **Step 4: Run the Node test and confirm GREEN**

Run the same command. Expected: all helper tests pass.

### Task 4: 159915 React Extension and Generic Shell Integration

**Files:**
- Create: `frontend/src/pages/backtest/strategy-extensions/Etf159915BacktestExtension.tsx`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`

- [ ] **Step 1: Build the focused extension components**

Implement three exported components:

```tsx
export function Etf159915RuleSummary() {
  return <section aria-label="159915 策略规则">{renderRuleGroups()}</section>
}
export function Etf159915DataReadiness({ query }: Props) {
  return <section aria-label="159915 数据检查">{renderReadiness(query)}</section>
}
export function Etf159915ExecutionTrace({ result }: { result: StrategyBacktestResult }) {
  return <ExecutionTable rows={buildEtf159915ExecutionRows(result.signal_diagnostics ?? [], result.fills ?? [])} />
}
```

Use existing `rounded-btn`, `border-border`, `bg-surface`, and typography tokens. Use lucide icons for status and disclosure. Keep the rules compact, render exact blocking reasons/warnings, and render a horizontally scrollable execution table on narrow screens.

- [ ] **Step 2: Integrate without changing generic behavior**

In `StrategyBacktest`:

- Request readiness only when 159915 and both dates are valid.
- Insert rule/readiness modules before the run action.
- Include readiness state in `canRunVnpy` and the run button's disabled reason.
- Extend `resultTab` with `execution`.
- Add the execution tab only when `result.strategy_info.id === 'etf_159915_minute'`.
- Reset `execution` to `daily` if a non-ETF result replaces the ETF result.

- [ ] **Step 3: Run frontend tests and type/build checks**

```powershell
node --test frontend\tests\etf-159915-backtest-extension.test.mjs frontend\tests\backtest-pools.test.mjs
pnpm --dir frontend build
```

Expected: tests pass and Vite production build exits 0.

### Task 5: End-to-End Verification

**Files:**
- Modify only files required by failures directly caused by Tasks 1-4.

- [ ] **Step 1: Run focused regression suites**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\vnpy_backtest\test_etf_159915_readiness.py backend\tests\vnpy_backtest\test_api.py backend\tests\vnpy_backtest\test_etf_159915_data_flow.py backend\tests\vnpy_backtest\test_etf_159915_execution.py backend\tests\vnpy_backtest\test_etf_159915_strategy.py -q
node --test frontend\tests\etf-159915-backtest-extension.test.mjs frontend\tests\backtest-pools.test.mjs
pnpm --dir frontend build
```

Expected: zero failures.

- [ ] **Step 2: Start the application and inspect desktop/mobile**

Start backend and frontend using the repository's existing development commands. Verify at desktop and mobile widths:

- Other strategies are visually and behaviorally unchanged.
- 159915 shows rules and readiness before run.
- The current 2025 data reports `14:59` sparsity as a warning, not a blocker.
- Blocking fixtures disable run with exact reasons.
- A completed 159915 result exposes execution trace rows without overflow or overlap.

- [ ] **Step 3: Review the final diff**

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only task-related files plus pre-existing user changes are present.
