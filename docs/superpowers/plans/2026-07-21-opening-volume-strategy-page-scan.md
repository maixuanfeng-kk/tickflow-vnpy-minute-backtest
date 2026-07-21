# Opening Volume Strategy Page Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote `opening_volume_portfolio` to a first-class minute strategy that users can edit, manually scan from the Strategy page against the TickFlow watchlist, and reuse unchanged in its portfolio backtest.

**Architecture:** Register one native `StrategyDef` whose metadata declares a minute-scanner backend and editable parameter schema. Keep the opening-volume signals in `app.backtest.minute_portfolio` as pure functions, used by both the new manual scan service and the portfolio service. The UI routes only this explicit backend to the minute scan/SSE APIs.

**Tech Stack:** FastAPI, pytest, React, TypeScript, TanStack Query, pnpm.

---

## File structure

- `backend/app/strategy/builtin/opening_volume_portfolio.py` — strategy metadata and minute-native backend declaration.
- `backend/app/strategy/engine.py` — register a minute-native strategy without a daily Polars filter.
- `backend/app/backtest/minute_portfolio.py` — typed parameters and shared, pure scan/entry/exit rules.
- `backend/app/api/strategy.py` — watchlist-only manual scan route.
- `backend/app/api/backtest.py` — resolve saved strategy parameters for the existing portfolio SSE route.
- `frontend/src/lib/api.ts` — typed manual minute-scan client.
- `frontend/src/pages/Screener.tsx` — detect minute-native strategy and run its scan.
- `frontend/src/components/screener/StrategySettingsDialog.tsx` — render time and boolean schema fields.
- `frontend/src/pages/backtest/StrategyBacktest.tsx` — select the registered ID, rather than a boolean-only special case.
- `backend/tests/test_opening_volume_strategy.py`, `backend/tests/test_opening_volume_strategy_scan_api.py`, `backend/tests/backtest/test_minute_portfolio.py`, `backend/tests/backtest/test_minute_portfolio_api.py` — regression coverage.

### Task 1: Register the minute strategy

**Files:**
- Create: `backend/app/strategy/builtin/opening_volume_portfolio.py`
- Modify: `backend/app/strategy/engine.py:113-390`
- Test: `backend/tests/test_opening_volume_strategy.py`
- Modify: `backend/tests/test_screener_etf.py:39-56`
- Modify: `backend/tests/backtest/test_matrix_strategy.py:245-270,643-670`

- [ ] **Step 1: Write the failing registration/defaults test**

```python
def test_opening_volume_strategy_is_registered_with_editable_defaults(engine):
    strategy = engine.get("opening_volume_portfolio")

    assert strategy.meta["timeframes"] == ["1m"]
    assert strategy.meta["scanner_backend"] == "opening_volume"
    assert {p["id"]: p["default"] for p in strategy.meta["params"]} == {
        "scan_start_time": "09:30", "scan_end_time": "09:59",
        "volume_multiple": 1.5, "enable_branch_a": True,
        "enable_branch_b": True, "enable_branch_c": True,
        "stop_loss_pct": 0.02, "ma_exit_period": 5,
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_opening_volume_strategy.py::test_opening_volume_strategy_is_registered_with_editable_defaults -v`

Expected: FAIL with `unknown strategy: opening_volume_portfolio`.

- [ ] **Step 3: Add the native strategy metadata and loader support**

```python
META = {
    "id": "opening_volume_portfolio",
    "name": "早盘放量组合",
    "asset_types": ["stock"],
    "timeframes": ["1m"],
    "scanner_backend": "opening_volume",
    "params": [
        {"id": "scan_start_time", "label": "扫描开始时间", "type": "time", "default": "09:30"},
        {"id": "scan_end_time", "label": "扫描结束时间", "type": "time", "default": "09:59"},
        {"id": "volume_multiple", "label": "量能倍数", "type": "float", "default": 1.5, "min": 0.1},
        {"id": "enable_branch_a", "label": "启用 A 入场分支", "type": "boolean", "default": True},
        {"id": "enable_branch_b", "label": "启用 B 入场分支", "type": "boolean", "default": True},
        {"id": "enable_branch_c", "label": "启用 C 入场分支", "type": "boolean", "default": True},
        {"id": "stop_loss_pct", "label": "止损比例", "type": "percent", "default": 0.02, "min": 0},
        {"id": "ma_exit_period", "label": "均线出场周期", "type": "int", "default": 5, "min": 1},
    ],
}
EXECUTION_BACKEND = "minute_native"
```

Extend `StrategyDef` with the backend, admit `minute_native` in `_load_file`, and require that backend to declare neither `filter` nor `filter_history`.
Update existing builtin-only assertions to keep validating the 18 daily matrix strategies while separately allowing exactly one `minute_native` builtin.

- [ ] **Step 4: Run the registration test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_opening_volume_strategy.py::test_opening_volume_strategy_is_registered_with_editable_defaults -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/strategy/builtin/opening_volume_portfolio.py backend/app/strategy/engine.py backend/tests/test_opening_volume_strategy.py
git commit -m "feat: register opening volume strategy"
```

### Task 2: Parameterize the shared minute rules

**Files:**
- Modify: `backend/app/backtest/minute_portfolio.py:1-270`
- Test: `backend/tests/backtest/test_minute_portfolio.py`

- [ ] **Step 1: Write a failing OR-entry test**

```python
def test_opening_volume_entry_requires_volume_and_any_enabled_branch():
    params = OpeningVolumeStrategyParams(
        scan_start_time="09:35", scan_end_time="09:45", volume_multiple=2.0,
        enable_branch_a=False, enable_branch_b=True, enable_branch_c=True,
    )
    assert is_opening_volume_entry(_row("09:34", volume_ratio=3, branch_b=True), params) is False
    assert is_opening_volume_entry(_row("09:40", volume_ratio=1.9, branch_b=True), params) is False
    assert is_opening_volume_entry(_row("09:40", volume_ratio=2.0, branch_b=True), params) is True
    assert is_opening_volume_entry(_row("09:40", volume_ratio=2.0, branch_a=True), params) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py -k opening_volume_entry -v`

Expected: FAIL during collection because `OpeningVolumeStrategyParams` and `is_opening_volume_entry` do not exist.

- [ ] **Step 3: Add a typed parameter object and pure predicate**

```python
@dataclass(frozen=True)
class OpeningVolumeStrategyParams:
    scan_start_time: str = "09:30"
    scan_end_time: str = "09:59"
    volume_multiple: float = 1.5
    enable_branch_a: bool = True
    enable_branch_b: bool = True
    enable_branch_c: bool = True
    stop_loss_pct: float = 0.02
    ma_exit_period: int = 5

def is_opening_volume_entry(row: Mapping[str, Any], params: OpeningVolumeStrategyParams) -> bool:
    return (
        params.scan_start_time <= row["time"] <= params.scan_end_time
        and row["volume_ratio"] >= params.volume_multiple
        and ((params.enable_branch_a and row["branch_a"])
             or (params.enable_branch_b and row["branch_b"])
             or (params.enable_branch_c and row["branch_c"]))
    )
```

Add `from_mapping` validation for time order, declared types and allowed MA periods. Replace fixed time, volume, stop-loss and MA constants in `MinutePortfolioEngine` with this parameter object.

- [ ] **Step 4: Run the minute-rule tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py -k opening_volume -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git commit -m "feat: parameterize opening volume rules"
```

### Task 3: Add the watchlist-only manual scan API

**Files:**
- Modify: `backend/app/api/strategy.py:1-220`
- Modify: `backend/app/backtest/minute_portfolio.py:194-270`
- Test: `backend/tests/test_opening_volume_strategy_scan_api.py`

- [ ] **Step 1: Write the failing endpoint contract test**

```python
def test_opening_volume_scan_uses_saved_params_and_watchlist_only(client, app, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.backtest.minute_portfolio.OpeningVolumeScanService.run",
        lambda self, config: captured.update(config=config) or {"rows": [], "total": 0},
    )

    response = client.post("/api/strategies/opening_volume_portfolio/scan", json={"as_of": "2026-07-20"})

    assert response.status_code == 200
    assert captured["config"].symbols is None
    assert captured["config"].strategy_params.volume_multiple == 1.5
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_opening_volume_strategy_scan_api.py::test_opening_volume_scan_uses_saved_params_and_watchlist_only -v`

Expected: FAIL with HTTP 404.

- [ ] **Step 3: Implement the native scanner route and service**

```python
@router.post("/{strategy_id}/scan")
def scan_native_strategy(strategy_id: str, body: NativeStrategyScanRequest, request: Request):
    strategy = request.app.state.strategy_engine.get(strategy_id)
    if strategy.execution_backend != "minute_native":
        raise HTTPException(400, "strategy does not support native minute scanning")
    saved = request.app.state.strategy_service.get_saved_params(strategy_id)
    params = OpeningVolumeStrategyParams.from_mapping(saved)
    return OpeningVolumeScanService(request.app.state.repo).run(
        OpeningVolumeScanConfig(as_of=body.as_of, strategy_params=params)
    )
```

The service snapshots the TickFlow watchlist, reads only those symbols’ required daily/minute history, returns normal result rows, returns an empty result for no candidates, and returns a precise 4xx error when data is unavailable.

- [ ] **Step 4: Run the endpoint tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_opening_volume_strategy_scan_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/strategy.py backend/app/backtest/minute_portfolio.py backend/tests/test_opening_volume_strategy_scan_api.py
git commit -m "feat: scan opening volume strategy from watchlist"
```

### Task 4: Support the registered strategy on the Strategy page

**Files:**
- Modify: `frontend/src/lib/api.ts:1320-1390`
- Modify: `frontend/src/pages/Screener.tsx:100-1040`
- Modify: `frontend/src/components/screener/StrategySettingsDialog.tsx:1-360`
- Test: the project’s established frontend test location for `Screener`

- [ ] **Step 1: Write the failing UI action test**

```tsx
it("runs the opening volume strategy through its watchlist scan endpoint", async () => {
  render(<Screener />)
  await userEvent.click(await screen.findByRole("button", { name: "早盘放量组合" }))
  await userEvent.click(screen.getByRole("button", { name: /运行扫描/ }))

  await waitFor(() => expect(api.scanNativeStrategy).toHaveBeenCalledWith(
    "opening_volume_portfolio", expect.anything(),
  ))
})
```

- [ ] **Step 2: Run the UI test to verify it fails**

Run: `pnpm --dir frontend test -- Screener.test.tsx`

Expected: FAIL because `scanNativeStrategy` is absent.

- [ ] **Step 3: Add the typed client and a minute-native UI branch**

```ts
scanNativeStrategy: async (strategyId: string, asOf?: string) =>
  request("/api/strategies/" + strategyId + "/scan", {
    method: "POST",
    body: JSON.stringify(asOf ? { as_of: asOf } : {}),
  }),
```

Use this route only when `strategy.execution_backend === "minute_native"`. Keep daily scanner behavior unchanged. Reuse `StrategySettingsDialog`; add `time` and `boolean` controls to its schema renderer so every declared strategy parameter is editable.

- [ ] **Step 4: Run the focused UI test and production build**

Run: `pnpm --dir frontend test -- Screener.test.tsx; pnpm --dir frontend build`

Expected: the test passes and the build exits 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/pages/Screener.tsx frontend/src/components/screener/StrategySettingsDialog.tsx <frontend-test-file>
git commit -m "feat: scan opening volume strategy from strategy page"
```

### Task 5: Reuse the same registered strategy in backtest

**Files:**
- Modify: `backend/app/api/backtest.py:597-670`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx:767-1490`
- Modify: `frontend/src/lib/backtestTask.ts:170-290`
- Test: `backend/tests/backtest/test_minute_portfolio_api.py`

- [ ] **Step 1: Write the failing resolved-parameters API test**

```python
def test_minute_portfolio_backtest_uses_resolved_strategy_params(client, app, monkeypatch):
    response = client.get("/api/backtest/minute-portfolio/stream", params={
        "strategy_id": "opening_volume_portfolio",
        "initial_capital": 10_000_000,
        "max_positions": 8,
        "params": '{"volume_multiple": 2.0}',
    })

    assert response.status_code == 200
    assert _captured_config().strategy_params.volume_multiple == 2.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio_api.py -k resolved_strategy_params -v`

Expected: FAIL because `MinutePortfolioConfig` does not contain resolved strategy parameters.

- [ ] **Step 3: Resolve the selected native strategy before dispatching SSE**

```python
strategy = request.app.state.strategy_engine.get(strategy_id)
if strategy.execution_backend != "minute_native":
    raise HTTPException(400, "strategy is not an opening-volume minute strategy")
strategy_params = OpeningVolumeStrategyParams.from_mapping(
    StrategyEngine.resolve_params(strategy, params=parse_json(params))
)
config = MinutePortfolioConfig(..., strategy_params=strategy_params)
```

Replace the `minutePortfolio` React state with normal strategy selection. Detect `minute_native` by the selected strategy detail, keep capital/maximum-position/cost controls as execution settings, and send the saved strategy parameters in the existing minute SSE request.

- [ ] **Step 4: Run focused API tests and frontend build**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio_api.py backend/tests/backtest/test_minute_portfolio.py -v; pnpm --dir frontend build`

Expected: all focused backend tests pass and Vite exits 0.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/backtest.py frontend/src/pages/backtest/StrategyBacktest.tsx frontend/src/lib/backtestTask.ts backend/tests/backtest/test_minute_portfolio_api.py
git commit -m "feat: reuse opening volume strategy in backtest"
```

### Task 6: Verify end-to-end behavior

**Files:**
- Modify only files needed to correct a verified regression from Tasks 1-5.

- [ ] **Step 1: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest`

Expected: exit 0 with no failures.

- [ ] **Step 2: Run the frontend production build**

Run: `pnpm --dir frontend build`

Expected: exit 0.

- [ ] **Step 3: Verify both browser flows**

1. On `/screener`, edit and save a time, volume, or branch value for “早盘放量组合”, then run scan and confirm the card states the TickFlow watchlist scope.
2. On `/backtest`, select the same strategy, confirm its saved parameters are used, and confirm initial capital, maximum positions, and costs remain independently editable execution inputs.

- [ ] **Step 4: Check and commit the final diff**

```bash
git diff --check
git status --short
git add <only files changed by this plan>
git commit -m "test: verify opening volume strategy workflows"
```

## Self-review

- Tasks 1-5 cover strategy-page visibility, editable saved parameters, watchlist-only manual scanning, configurable window/volume/branches/stop loss/MA exit, OR semantics, and shared strategy-ID backtesting.
- Automatic monitoring, notifications, polling, and unrelated daily-strategy changes are intentionally out of scope.
- The plan contains no unfinished placeholder instructions or vague implementation steps.
