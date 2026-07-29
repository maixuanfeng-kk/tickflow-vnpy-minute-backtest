# vn.py Native Minute Portfolio Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make vn.py a selectable execution backend for the existing opening-volume strategy while both backends share one configuration, business-rule contract, and realistic A-share execution policy.

**Architecture:** Add a dependency-free shared opening-volume module for normalized configuration, candidate evaluation, ranking, market rules, and fill-policy helpers. Keep native dictionary replay and vn.py `BarData` replay as separate adapters, but make the page and main API submit the same normalized request to either one.

**Tech Stack:** Python 3.11, FastAPI SSE, Polars, vn.py `BarData`, React 18, TypeScript, Vite, pytest, Node test runner.

---

## File Structure

- Create `backend/app/backtest/opening_volume_shared.py`: normalized shared settings, candidate logic, participation parsing, and market-rule helpers with no Polars/vn.py import.
- Modify `backend/app/backtest/minute_portfolio.py`: delegate strategy rules and market execution constraints to the shared module; add sell participation support.
- Modify `backend/app/vnpy_backtest/portfolio.py`: accept the shared configuration semantics, deterministic shared selection, dynamic reserve, and same-day next-actual-bar expiry.
- Modify `backend/app/vnpy_backtest/strategies/opening_breakout_pool.py`: adapt `BarData` and `DailyReference` into shared candidate/exit decisions without changing the legacy registry endpoint.
- Modify `backend/app/vnpy_backtest/service.py`: accept the normalized opening-volume contract and return the common result shape.
- Modify `backend/app/api/backtest.py`: parse one configuration and dispatch `engine=native|vnpy`; retain `/vnpy/stream` as a compatibility adapter.
- Modify `frontend/src/lib/backtestTask.ts`: serialize common execution settings and route the primary task through the unified stream.
- Modify `frontend/src/pages/backtest/StrategyBacktest.tsx`: remove vn.py-only strategy/10% controls, preserve one form on engine switch, and show read-only vn.py data-source status.
- Modify tests under `backend/tests/backtest/`, `backend/tests/vnpy_backtest/`, and `frontend/src/pages/backtest/openingVolumeSettings.test.ts`.

### Task 1: Establish Shared Contract Tests

**Files:**
- Create: `backend/tests/backtest/test_opening_volume_shared.py`
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/tests/vnpy_backtest/test_portfolio_framework.py`

- [ ] **Step 1: Write failing shared-rule tests**

```python
from app.backtest.opening_volume_shared import (
    SharedExecutionConfig,
    evaluate_opening_volume_entry,
    next_actual_bar_time,
    rule_for_symbol,
)


def test_st_fallback_limit_is_ten_percent() -> None:
    assert rule_for_symbol("600000.SH", name="*ST 示例").price_limit_pct == 0.10


def test_entry_keeps_native_high_breakout_and_volume_ranking() -> None:
    result = evaluate_opening_volume_entry(
        previous_open=11, previous_close=10, previous_high=11,
        previous_change_pct=-0.01, today_close=10.9, minute_high=11.01,
        volume_ratio=1.5, params={"enable_branch_a": True},
    )
    assert result.primary_reason == "previous_bearish_breakout"


def test_next_actual_bar_expires_at_end_of_day() -> None:
    assert next_actual_bar_time([time(9, 30), time(13, 0)], time(9, 30)) == time(13, 0)
    assert next_actual_bar_time([time(9, 30)], time(9, 30)) is None
```

- [ ] **Step 2: Run the focused test module and verify import failure**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/backtest/test_opening_volume_shared.py -q`

Expected: FAIL because `app.backtest.opening_volume_shared` does not exist.

- [ ] **Step 3: Extend existing tests with the shared execution policy expectations**

```python
def test_native_uses_minimum_commission_and_sell_volume_limit() -> None:
    config = MinutePortfolioConfig(
        symbols=["600000.SH"], initial_capital=10_000,
        min_commission=5, max_buy_volume_ratio=1.0,
        max_sell_volume_ratio=0.5,
    )
    # Fixture creates a 1,000-share position and a 600-share sell request.
    assert MinutePortfolioEngine(config).run(rows, contexts)["trades"][0]["shares"] == 500
```

- [ ] **Step 4: Run both existing modules and capture expected failures**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio.py tests/vnpy_backtest/test_portfolio_framework.py -q`

Expected: New assertions fail; unrelated existing tests still collect.

- [ ] **Step 5: Commit the failing test baseline**

```powershell
git add backend/tests/backtest/test_opening_volume_shared.py backend/tests/backtest/test_minute_portfolio.py backend/tests/vnpy_backtest/test_portfolio_framework.py
git commit -m "test(backtest): define native vnpy parity contract"
```

### Task 2: Implement the Dependency-Free Shared Rules Module

**Files:**
- Create: `backend/app/backtest/opening_volume_shared.py`
- Modify: `backend/app/backtest/minute_portfolio.py`
- Test: `backend/tests/backtest/test_opening_volume_shared.py`

- [ ] **Step 1: Add normalized shared types and market rules**

```python
@dataclass(frozen=True)
class SharedExecutionConfig:
    min_commission: float = 5.0
    max_buy_volume_ratio: float | None = 1.0
    max_sell_volume_ratio: float | None = 1.0


def rule_for_symbol(symbol: str, *, name: str = "", limit_pct: float | None = None) -> AShareTradingRule:
    # Metadata wins. ST is 10% when metadata is unavailable.
    ...
```

- [ ] **Step 2: Move pure native entry and ranking behavior into shared functions**

```python
def evaluate_opening_volume_entry(... ) -> EntryDecision | None:
    matched = tuple(branch for branch, passed in checks if passed)
    if not matched:
        return None
    return EntryDecision(primary_reason=matched[0], matched_reasons=matched)


def rank_candidates(rows: list[Candidate], mode: str, ...) -> list[Candidate]:
    # Preserve existing volume-ratio, score, and watchlist ordering.
    ...
```

- [ ] **Step 3: Replace only imports/calls in the native engine**

```python
from app.backtest.opening_volume_shared import (
    evaluate_opening_volume_entry,
    rank_candidates,
    rule_for_symbol,
)
```

Keep public imports from `minute_portfolio.py` as forwarding imports so existing API and
tests remain compatible.

- [ ] **Step 4: Run shared and native tests**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/backtest/test_opening_volume_shared.py tests/backtest/test_minute_portfolio.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the shared core**

```powershell
git add backend/app/backtest/opening_volume_shared.py backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_opening_volume_shared.py backend/tests/backtest/test_minute_portfolio.py
git commit -m "feat(backtest): share opening volume rules"
```

### Task 3: Make Native Execution Use the Shared Market Policy

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py`
- Modify: `backend/app/api/backtest.py`
- Test: `backend/tests/backtest/test_minute_portfolio.py`
- Test: `backend/tests/backtest/test_minute_portfolio_api.py`

- [ ] **Step 1: Add failing API tests for the two participation fields and minimum commission**

```python
response = await backtest.minute_portfolio_stream(
    request, start="2026-01-05", end="2026-01-06",
    max_buy_volume_ratio=1.0, max_sell_volume_ratio=0.5, min_commission=5,
)
assert captured["config"].max_buy_volume_ratio == 1.0
assert captured["config"].max_sell_volume_ratio == 0.5
assert captured["config"].min_commission == 5
```

- [ ] **Step 2: Run API tests and verify missing parameters fail**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio_api.py -q`

Expected: FAIL in the new config assertions.

- [ ] **Step 3: Add shared execution fields and apply them to buy/sell fills**

```python
commission = max(turnover * self.config.commission_pct, self.config.min_commission)
shares = min(shares, _capacity(bar, symbol, self.config.max_buy_volume_ratio))
sell_shares = min(position["shares"], _capacity(bar, symbol, self.config.max_sell_volume_ratio))
```

Use `rule_for_symbol` for price limits and lot/first-buy minimum. Pending next-bar orders
must resolve to the next actual same-day bar and expire if none exists.

- [ ] **Step 4: Run native engine and API regression tests**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio.py tests/backtest/test_minute_portfolio_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit native execution alignment**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/app/api/backtest.py backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py
git commit -m "feat(backtest): align native market execution rules"
```

### Task 4: Adapt the vn.py Portfolio Path to Shared Rules

**Files:**
- Modify: `backend/app/vnpy_backtest/market_rules.py`
- Modify: `backend/app/vnpy_backtest/portfolio.py`
- Modify: `backend/app/vnpy_backtest/strategies/opening_breakout_pool.py`
- Modify: `backend/app/vnpy_backtest/service.py`
- Test: `backend/tests/vnpy_backtest/test_portfolio_framework.py`
- Test: `backend/tests/vnpy_backtest/test_service.py`

- [ ] **Step 1: Write vn.py failing tests for native-equivalent candidate and exit behavior**

```python
strategy = OpeningBreakoutPoolStrategy({"scan_end_time": "09:59"})
intents = strategy.on_minute(bars, context)
assert intents[0].diagnostic["primary_reason"] == "previous_bearish_breakout"
assert intents[0].diagnostic["matched_conditions"] == ["previous_bearish_breakout"]
```

Also add a test proving equal sizing uses current equity and a 3% dynamic reserve after
the first position gains value.

- [ ] **Step 2: Run vn.py tests and verify the new native-equivalence assertions fail**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/vnpy_backtest/test_portfolio_framework.py tests/vnpy_backtest/test_service.py -q`

Expected: FAIL because the current strategy uses close-crossing, 10:00, and matched-count priority.

- [ ] **Step 3: Replace strategy-only rules with shared decisions**

```python
decision = evaluate_opening_volume_entry(...)
if decision is not None:
    intents.append(OrderIntent(
        symbol, Direction.LONG, decision.primary_reason,
        diagnostic={"matched_conditions": list(decision.matched_reasons)},
    ))
```

Delete vn.py-only random priority. Convert candidate diagnostics into shared candidate
rows, call the shared ranker, then queue only the selected symbols.

- [ ] **Step 4: Align vn.py execution semantics**

```python
self.reserve_cash = current_equity * self.reserve_ratio
target = current_equity * (1 - self.reserve_ratio) / self.max_positions
```

Use shared market rules, buy/sell participation values, minimum commission, shared MA
exit semantics, and same-day next-actual-bar expiry. Keep `BarData` conversion and
daily partition streaming unchanged.

- [ ] **Step 5: Normalize the vn.py result payload**

```python
return {
    "trades": completed_trades,
    "open_positions": positions,
    "signal_diagnostics": diagnostics,
    "rejections": rejections,
    "execution": execution_counts,
    ...,
}
```

- [ ] **Step 6: Run vn.py regression tests**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/vnpy_backtest -q`

Expected: PASS.

- [ ] **Step 7: Commit vn.py alignment**

```powershell
git add backend/app/vnpy_backtest/market_rules.py backend/app/vnpy_backtest/portfolio.py backend/app/vnpy_backtest/strategies/opening_breakout_pool.py backend/app/vnpy_backtest/service.py backend/tests/vnpy_backtest/test_portfolio_framework.py backend/tests/vnpy_backtest/test_service.py
git commit -m "feat(vnpy): align opening volume portfolio rules"
```

### Task 5: Unify the Main API and Preserve Legacy vn.py Calls

**Files:**
- Modify: `backend/app/api/backtest.py`
- Test: `backend/tests/backtest/test_minute_portfolio_api.py`
- Test: `backend/tests/vnpy_backtest/test_api.py`

- [ ] **Step 1: Write failing dispatch and compatibility tests**

```python
response = await backtest.minute_portfolio_stream(
    request, start="2026-01-05", end="2026-01-06", engine="vnpy",
    params='{"enable_branch_b": false}',
)
assert captured["config"].strategy_id == "opening_volume_portfolio"
assert captured["config"].strategy_params.enable_branch_b is False
```

- [ ] **Step 2: Run API tests and verify `engine` is currently unsupported**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio_api.py tests/vnpy_backtest/test_api.py -q`

Expected: FAIL in the new vn.py dispatch test.

- [ ] **Step 3: Add one normalized parser and engine dispatch**

```python
def _parse_opening_volume_request(...) -> NormalizedOpeningVolumeRequest:
    ...

if engine == "native":
    result = MinutePortfolioService(repo).run(native_config, progress_callback=emit)
elif engine == "vnpy":
    result = VnpyMinuteBacktestService(repo).run(vnpy_config)
else:
    raise HTTPException(status_code=400, detail="engine must be native or vnpy")
```

Map the legacy `/vnpy/stream` request to the normalized parser before dispatch. Keep its
current endpoint name and SSE response shape.

- [ ] **Step 4: Add standard-data availability details to vn.py no-data errors**

```python
raise ValueError(
    f"所选日期范围内没有本地分钟 K 数据；标准数据源: {source}；可用日期: {available_range}"
)
```

- [ ] **Step 5: Run API tests**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/backtest/test_minute_portfolio_api.py tests/vnpy_backtest/test_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit API unification**

```powershell
git add backend/app/api/backtest.py backend/tests/backtest/test_minute_portfolio_api.py backend/tests/vnpy_backtest/test_api.py
git commit -m "feat(api): unify native and vnpy minute requests"
```

### Task 6: Replace the vn.py-Specific UI With the Shared Form

**Files:**
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`
- Modify: `frontend/src/lib/backtestTask.ts`
- Modify: `frontend/src/pages/backtest/openingVolumeSettings.test.ts`

- [ ] **Step 1: Add failing source-level settings tests**

```typescript
assert.doesNotMatch(backtestSource, /vnpyStrategyId/)
assert.doesNotMatch(backtestSource, /volumeLimitEnabled/)
assert.match(backtestSource, /engine: engineMode === 'vnpy' \? 'vnpy' : 'native'/)
assert.match(backtestSource, /买入成交量上限/)
assert.match(backtestSource, /卖出成交量上限/)
```

- [ ] **Step 2: Run the frontend settings test and verify it fails**

Run: `cd frontend; node --test src/pages/backtest/openingVolumeSettings.test.ts`

Expected: FAIL because the vn.py-only controls still exist.

- [ ] **Step 3: Remove the separate vn.py panel and retain engine selection**

```tsx
<button onClick={() => setEngineMode('native')}>原生分钟组合</button>
<button onClick={() => setEngineMode('vnpy')}>vn.py 分钟组合</button>
```

Do not reset `strategyParams`, overrides, capital, dates, or stock-pool text when
switching engines. Render the existing opening-volume cards and advanced tabs for both.

- [ ] **Step 4: Add shared execution fields and vn.py source status**

```tsx
<input value={maxBuyVolumeRatio} onChange={...} aria-label="买入成交量上限" />
<input value={maxSellVolumeRatio} onChange={...} aria-label="卖出成交量上限" />
{engineMode === 'vnpy' && <p>使用项目标准分钟数据分区</p>}
```

Send one `startBacktest` request with `engine`, all common parameters, and the common
strategy id. Do not send `minute_data_dir` for vn.py.

- [ ] **Step 5: Run settings test and production build**

Run: `cd frontend; node --test src/pages/backtest/openingVolumeSettings.test.ts; corepack pnpm build`

Expected: PASS.

- [ ] **Step 6: Commit the shared UI**

```powershell
git add frontend/src/pages/backtest/StrategyBacktest.tsx frontend/src/lib/backtestTask.ts frontend/src/pages/backtest/openingVolumeSettings.test.ts
git commit -m "feat(frontend): share native vnpy opening volume form"
```

### Task 7: Prove Cross-Engine Parity and Deliver

**Files:**
- Create: `backend/tests/backtest/test_opening_volume_engine_parity.py`
- Modify: `docs/delivery-records/2026-07-29-vnpy-breakout-coexist.md`

- [ ] **Step 1: Write a deterministic parity fixture and failing assertions**

```python
def test_native_and_vnpy_match_for_same_normalized_minute_bars() -> None:
    native = run_native_fixture(config)
    vnpy = run_vnpy_fixture(config)
    assert normalize(native["trades"]) == normalize(vnpy["trades"])
    assert native["open_positions"] == vnpy["open_positions"]
    assert native["equity_curve"] == vnpy["equity_curve"]
```

- [ ] **Step 2: Run the parity test and verify it exposes any remaining divergence**

Run: `cd backend; .venv\Scripts\python.exe -m pytest tests/backtest/test_opening_volume_engine_parity.py -q`

Expected: FAIL until both adapters expose identical observable results.

- [ ] **Step 3: Close remaining deterministic differences without changing shared rules**

Only adjust adapters, result normalization, or fixture conversion. Do not add
engine-specific ordering, defaults, or strategy exceptions.

- [ ] **Step 4: Run the full affected backend and frontend verification set**

Run:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/backtest/test_opening_volume_shared.py tests/backtest/test_opening_volume_engine_parity.py tests/backtest/test_minute_portfolio.py tests/backtest/test_minute_portfolio_api.py tests/vnpy_backtest -q
cd ..\frontend
node --test src/pages/backtest/openingVolumeSettings.test.ts
corepack pnpm build
```

Expected: all selected tests and the frontend build pass.

- [ ] **Step 5: Update delivery record and commit**

```powershell
git add backend/tests/backtest/test_opening_volume_engine_parity.py docs/delivery-records/2026-07-29-vnpy-breakout-coexist.md
git commit -m "test(backtest): verify native vnpy parity"
```

## Plan Self-Review

- Spec coverage: Tasks 1-4 cover shared rules, realistic market mechanics, and both
  executors; Task 5 covers unified API and compatibility; Task 6 covers the unified
  form; Task 7 proves parity and runs required regression checks.
- Placeholder scan: no deferred implementation markers are used; each task names
  files, expected behavior, test command, and commit scope.
- Type consistency: shared execution values are named `min_commission`,
  `max_buy_volume_ratio`, and `max_sell_volume_ratio` throughout. `engine` has only
  `native` and `vnpy` values on the unified route.
