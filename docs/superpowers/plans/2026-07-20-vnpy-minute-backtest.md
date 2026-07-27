# vn.py Minute CTA Backtest Implementation Plan

> **已替代（2026-07-27）：** 本计划描述的单标的分钟双均线 CTA 引擎已由“本地 CSV + 股票池组合撮合 + 策略注册表”框架替代，仅保留作历史审计参考。

> **已替代（2026-07-27）：** 本计划描述的单标的分钟双均线 CTA 引擎已由“本地 CSV + 股票池组合撮合 + 策略注册表”框架替代，仅保留作历史审计参考。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a single-stock, local-CSV, minute CTA backtest powered by vn.py through TickFlow's existing SSE workflow.

**Architecture:** Keep Matrix backtests unchanged. Add a `VnpyMinuteBacktestService` that loads the existing local CSV bars, invokes the existing local vn.py adapter, and maps vn.py statistics/trades to the result shape already consumed by `StrategyBacktest`. Route it through a dedicated SSE endpoint and select that endpoint only when the user chooses the vn.py engine.

**Tech Stack:** Python 3.11+, FastAPI/SSE, vn.py 4.4 + vnpy-ctastrategy, Polars local CSV provider, React 18/TypeScript/TanStack Query.

---

### Task 1: Preserve both sides of the pending minute-backtest merge

**Files:**
- Modify: `backend/app/api/backtest.py`
- Modify: `backend/app/backtest/strategy.py`
- Modify: `backend/app/backtest/engine.py`
- Modify: `backend/app/api/kline.py`
- Modify: `backend/app/indicators/pipeline.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/preferences.py`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/backtestTask.ts`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`
- Modify: `frontend/src/components/data/MinuteSyncConfig.tsx`
- Modify: `backend/app/strategy/builtin/boll_breakout.py`

- [ ] **Step 1: Resolve the conflicts as unions, not side selection**

  Preserve upstream `asset_type`, `minute_fill`, signal-id fields, and matrix-cache setup together with local `frequency`, `max_hold_bars`, local CSV provider, and minute timestamps. The request/config boundary must include both sets:

  ```python
  frequency: Literal["1d", "1m"] = "1d"
  max_hold_bars: int | None = Field(default=None, ge=1)
  asset_type: str = "stock"
  minute_fill: bool = False
  ```

- [ ] **Step 2: Include every execution-affecting option in the SSE job key and config**

  `_make_job_key()` and `StrategyBacktestConfig(...)` must retain `asset_type`,
  `minute_fill`, `frequency`, and `max_hold_bars`; do not allow two distinct jobs to
  share a reconnect key.

- [ ] **Step 3: Verify the merge is syntactically clean**

  Run: `rg -n '^(<<<<<<<|=======|>>>>>>>)' backend frontend`

  Expected: no output.

- [ ] **Step 4: Run the directly affected existing tests**

  Run: `cd backend; python -m pytest tests/backtest/test_minute_panel.py tests/backtest/test_minute_simulation.py tests/api/test_backtest_minute.py tests/api/test_kline_minute_local.py -q`

  Expected: PASS.

### Task 2: Make vn.py an explicit optional runtime capability

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `Dockerfile`
- Modify: `backend/requirements-vnpy.txt`
- Test: `backend/tests/vnpy_backtest/test_vnpy_engine.py`

- [ ] **Step 1: Write the failing availability test**

  Add a test that imports the adapter's availability helper and asserts that it either
  returns `(True, None)` or a stable message naming both required packages:

  ```python
  available, message = vnpy_available()
  assert available or message == "vn.py minute backtest requires vnpy and vnpy-ctastrategy"
  ```

- [ ] **Step 2: Add an optional dependency group**

  Add the exact existing constraints to `pyproject.toml`:

  ```toml
  [project.optional-dependencies]
  vnpy-backtest = ["vnpy>=4.4", "vnpy-ctastrategy>=1.4"]
  ```

  Keep the default backend install free of GUI-heavy vn.py packages. Make Docker's
  feature build opt-in, not a default requirement.

- [ ] **Step 3: Run the vn.py adapter tests**

  Run: `cd backend; python -m pytest tests/vnpy_backtest -q`

  Expected: PASS when the optional packages are installed; otherwise the API will
  report the stable capability message rather than fail at module import.

### Task 3: Implement a focused vn.py minute-backtest service

**Files:**
- Create: `backend/app/vnpy_backtest/service.py`
- Modify: `backend/app/vnpy_backtest/__init__.py`
- Modify: `backend/app/vnpy_backtest/engine.py`
- Modify: `backend/app/vnpy_backtest/trades.py`
- Test: `backend/tests/vnpy_backtest/test_service.py`

- [ ] **Step 1: Write failing service tests**

  Cover one complete run, invalid multi-symbol input, and no local bars:

  ```python
  with pytest.raises(ValueError, match="exactly one symbol"):
      service.run(VnpyMinuteBacktestConfig(symbols=["000001.SZ", "600000.SH"], ...))

  with pytest.raises(ValueError, match="no local minute bars"):
      service.run(VnpyMinuteBacktestConfig(symbols=["000001.SZ"], ...))
  ```

- [ ] **Step 2: Implement an immutable request model**

  Define `VnpyMinuteBacktestConfig` with `symbol`, `start`, `end`,
  `initial_capital`, `commission_pct`, `stamp_tax_pct`, `slippage_bps`,
  `lot_size`, `max_volume_ratio`, and `params`. Reject reversed dates and do not
  accept a symbol list in the service boundary.

- [ ] **Step 3: Replay injected bars through the existing adapter**

  Use `load_local_minute_bars()`, configure the existing `LocalNextBarOpenEngine`,
  set `engine.history_data = bars`, add `MinuteDoubleMaVolumeStrategy`, then call:

  ```python
  engine.run_backtesting()
  daily = engine.calculate_result()
  stats = engine.calculate_statistics(output=False)
  trades = summarize_trades(list(engine.trades.values()), bars, ...)
  ```

  Do not monkey-patch vn.py's module-global loader in a web request.

- [ ] **Step 4: Normalize the result**

  Return `stats`, `equity_curve`, `trades`, `strategy_info`, and `config`. Use
  `stats["total_return"]` as a decimal fraction, map each equity point to
  `{"date": "YYYY-MM-DD HH:MM", "value": float}`, and add
  `config={"engine": "vnpy", "frequency": "1m", ...}`.

- [ ] **Step 5: Run service tests**

  Run: `cd backend; python -m pytest tests/vnpy_backtest/test_service.py -q`

  Expected: PASS.

### Task 4: Expose an SSE endpoint without changing Matrix routes

**Files:**
- Modify: `backend/app/api/backtest.py`
- Test: `backend/tests/api/test_vnpy_backtest.py`

- [ ] **Step 1: Write the failing API tests**

  Test `GET /api/backtest/vnpy/stream` with valid parameters, a comma-separated
  two-symbol request, and missing local data. Assert that success emits `progress`
  before `done`, and invalid requests emit an SSE `error` with a readable message.

- [ ] **Step 2: Add `vnpy_stream()`**

  Reuse `_BacktestJob`, `_running_jobs`, cancellation, and TTL replay mechanics, but
  use a separate job key namespace such as `vnpy:{md5}`. Start the service in a daemon
  thread, append `{"day": 0, "total": 1, "date": "加载分钟K"}` before data loading,
  then `{"day": 1, "total": 1, "date": "完成"}` before `_finish_job()`.

- [ ] **Step 3: Run API tests**

  Run: `cd backend; python -m pytest tests/api/test_vnpy_backtest.py -q`

  Expected: PASS.

### Task 5: Route vn.py selections from the backtest page

**Files:**
- Modify: `frontend/src/lib/backtestTask.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`
- Test: `frontend/src/lib/backtestTask.test.ts` (or the configured frontend test location)

- [ ] **Step 1: Add a pure URL-builder test**

  Verify a vn.py request builds `/api/backtest/vnpy/stream`, carries a single symbol,
  serializes strategy parameters once, and rejects a second symbol before creating an
  `EventSource`.

- [ ] **Step 2: Extend the request type with an engine discriminator**

  ```ts
  engine?: 'matrix' | 'vnpy'
  ```

  In `startBacktest`, choose `/api/backtest/vnpy/stream` only for `engine === 'vnpy'`;
  preserve the existing `/strategy/stream` URL byte-for-byte for Matrix requests.

- [ ] **Step 3: Add a minute-only engine selector**

  Default to `matrix`. Selecting `vnpy` forces one entered symbol and presents only
  the supported CTA strategy/parameters. The daily UI must not render the selector.

- [ ] **Step 4: Build the frontend**

  Run: `cd frontend; pnpm build`

  Expected: TypeScript and Vite build succeed.

### Task 6: Verify the integrated feature and document installation

**Files:**
- Modify: `README.md`
- Modify: `docs/configuration.md`
- Test: `backend/tests/vnpy_backtest/test_service.py`
- Test: `backend/tests/api/test_vnpy_backtest.py`

- [ ] **Step 1: Document opt-in installation and CSV constraints**

  State the exact install command, that the feature needs local per-symbol minute CSV
  files, supports one A-share stock per run, uses next-bar-open fills, and does not
  enable broker/live-trading access.

- [ ] **Step 2: Run focused verification**

  Run: `cd backend; python -m pytest tests/vnpy_backtest tests/api/test_vnpy_backtest.py tests/backtest/test_minute_panel.py tests/backtest/test_minute_simulation.py -q`

  Run: `cd frontend; pnpm build`

  Expected: all focused backend tests pass and the frontend build succeeds.

- [ ] **Step 3: Review the final diff**

  Run: `git diff --check; git status --short`

  Expected: no whitespace errors; only the intended local-minute, vn.py, test, and
  documentation changes remain.
