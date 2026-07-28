# Opening Volume Execution Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make candidate sorting, minute entry/exit fills, and end-of-backtest liquidation configurable and truthful for the opening-volume minute portfolio, while enforcing T+1, suspension, and one-price limit constraints.

**Architecture:** Extend the existing `MinutePortfolioConfig` and dedicated SSE endpoint rather than introducing a second engine. Keep signal generation unchanged; isolate candidate ordering, order execution, market tradability, and terminal valuation inside small helpers in `minute_portfolio.py`. Reuse the existing backtest-page controls, add only the requested sorting and forced-liquidation fields, and return open positions separately from completed trades.

**Tech Stack:** Python 3.11, FastAPI, pytest, React 18, TypeScript, Vite, Node test runner.

---

## File map

- `backend/app/backtest/minute_portfolio.py`: configuration, candidate ordering, minute order execution, market constraints, terminal valuation, open-position output, and statistics.
- `backend/app/api/backtest.py`: dedicated minute-portfolio query validation, job identity, configuration construction, and backward-compatible defaults.
- `backend/tests/backtest/test_minute_portfolio.py`: pure ranking and engine behavior tests.
- `backend/tests/backtest/test_minute_portfolio_api.py`: SSE parameter validation and configuration propagation tests.
- `frontend/src/lib/backtestTask.ts`: typed request fields and SSE query serialization.
- `frontend/src/lib/api.ts`: open-position result shape.
- `frontend/src/pages/backtest/StrategyBacktest.tsx`: strategy-specific labels, controls, persistence, request mapping, equal-weight explanation, and terminal-position display.
- `frontend/src/pages/backtest/openingVolumeSettings.test.ts`: request and source-level UI regression tests.

### Task 1: Candidate ordering modes

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`

- [ ] **Step 1: Write failing tests for volume, score, and watchlist order**

Add tests that exercise a public deterministic dispatcher rather than duplicating sort keys:

```python
def test_rank_candidates_supports_all_user_sort_modes() -> None:
    rows = [
        {"symbol": "600000.SH", "volume_ratio": 3.0, "today_return": 0.01},
        {"symbol": "000001.SZ", "volume_ratio": 2.0, "today_return": 0.05},
    ]

    assert [row["symbol"] for row in rank_candidates(
        rows, mode="volume_ratio",
    )] == ["600000.SH", "000001.SZ"]
    assert [row["symbol"] for row in rank_candidates(
        rows,
        mode="score",
        weights={"volume_ratio": 0.0, "today_return": 1.0},
    )] == ["000001.SZ", "600000.SH"]
    assert [row["symbol"] for row in rank_candidates(
        rows,
        mode="watchlist_order",
        watchlist_order={"000001.SZ": 0, "600000.SH": 1},
    )] == ["000001.SZ", "600000.SH"]
```

Keep the existing tie-break regression for volume ratio, return, and symbol.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py -k "rank_candidates" -q
```

Expected: FAIL because `rank_candidates` does not accept `mode`, `weights`, or `watchlist_order`.

- [ ] **Step 3: Implement the minimal ordering dispatcher**

Add allowed values and extend the existing function without changing its default:

```python
CANDIDATE_SORT_MODES = {"score", "volume_ratio", "watchlist_order"}


def rank_candidates(
    rows: list[Candidate],
    *,
    mode: str = "volume_ratio",
    weights: Mapping[str, float] | None = None,
    score_min: float | None = None,
    score_max: float | None = None,
    watchlist_order: Mapping[str, int] | None = None,
) -> list[Candidate]:
    if mode == "score":
        return _score_candidates(rows, weights or {}, score_min, score_max)
    if mode == "watchlist_order":
        order = watchlist_order or {}
        fallback = len(order)
        return sorted(rows, key=lambda row: (order.get(row["symbol"], fallback), row["symbol"]))
    return sorted(
        rows,
        key=lambda row: (-row["volume_ratio"], -row["today_return"], row["symbol"]),
    )
```

Move `_score_candidates` above the dispatcher or keep a thin private dispatcher below both functions so Python name resolution is valid at call time. Add `candidate_sort: str = "volume_ratio"` to `MinutePortfolioConfig`, and call the dispatcher whenever candidates exceed available slots. Preserve the existing rule that score range filtering is only applied when the candidate count exceeds remaining slots.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py -k "rank_candidates or scores_same_minute or does_not_score" -q
```

Expected: PASS.

- [ ] **Step 5: Commit candidate ordering**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "feat(backtest): add opening volume candidate ordering"
```

### Task 2: Minute fill semantics and one-minute buy validity

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`

- [ ] **Step 1: Write failing fill and expiration tests**

Add a two-day fixture so the final-day entry restriction introduced later does not obscure fill behavior. Cover both entry prices and both exit prices:

```python
@pytest.mark.parametrize(
    ("entry_fill", "expected_time", "expected_price"),
    [
        ("signal_minute_close", "09:30:00", 10.2),
        ("next_minute_open", "09:31:00", 10.3),
    ],
)
def test_engine_uses_configured_minute_entry_fill(
    entry_fill: str,
    expected_time: str,
    expected_price: float,
) -> None:
    trade = _single_entry_trade(
        execution_volume=20_000.0,
        entry_fill=entry_fill,
    )
    assert trade["entry_datetime"].endswith(expected_time)
    assert trade["entry_price"] == expected_price


def test_failed_next_minute_buy_expires_and_a_later_signal_can_retry() -> None:
    result = _retry_after_failed_next_minute_buy_result()
    assert result["trades"][0]["entry_datetime"].endswith("09:33:00")
```

Define the retry fixture explicitly:

```python
def _retry_after_failed_next_minute_buy_result() -> dict:
    symbol = "600000.SH"
    day = date(2026, 1, 5)
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "cumulative_volume": 150.0,
            "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
            "volume": 0.0, "cumulative_volume": 150.0,
            "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 32),
            "open": 10.3, "high": 10.7, "low": 10.2, "close": 10.4,
            "volume": 300.0, "cumulative_volume": 450.0,
            "previous_cumulative_volume": 250.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 33),
            "open": 10.5, "high": 10.6, "low": 10.4, "close": 10.5,
            "volume": 10_000.0, "cumulative_volume": 10_450.0,
            "previous_cumulative_volume": 300.0,
        },
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": 10.0,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_closes": [9.0, 9.0, 9.0, 9.0],
    }}
    return MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(
            enable_branch_b=False,
            enable_branch_c=False,
        ),
    )).run(rows, contexts)
```

Use the existing overnight risk fixture to test exit fills:

```python
@pytest.mark.parametrize(
    ("exit_fill", "expected_time"),
    [
        ("signal_minute_close", "2026-01-06 09:30:00"),
        ("next_minute_open", "2026-01-06 09:31:00"),
    ],
)
def test_engine_uses_configured_minute_exit_fill(
    exit_fill: str,
    expected_time: str,
) -> None:
    trade = _risk_control_trade(
        day_two_close=8.0,
        stop_loss_pct=0.10,
        exit_fill=exit_fill,
    )
    assert trade["exit_datetime"] == expected_time
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py -k "configured_minute_entry_fill or configured_minute_exit_fill or failed_next_minute_buy" -q
```

Expected: FAIL because the engine is fixed to next-minute open and pending buys do not expire after one minute.

- [ ] **Step 3: Add fill configuration and single-use pending orders**

Extend the configuration:

```python
entry_fill: str = "next_minute_open"
exit_fill: str = "next_minute_open"
```

Store the exact execution timestamp when queuing a next-minute buy:

```python
pending_buys.append({
    **candidate,
    "execution_timestamp": timestamp + timedelta(minutes=1),
})
```

At every engine timestamp, remove every order whose `execution_timestamp <= timestamp`. Execute only when it equals the current timestamp and the symbol has a valid bar. Mark `entered_today` only after a successful fill, so a failed order does not suppress a later fresh signal.

Extract focused helpers:

```python
def _entry_base_price(bar: Mapping[str, Any], fill: str) -> float:
    key = "close" if fill == "signal_minute_close" else "open"
    return float(bar.get(key) or 0)


def _exit_base_price(bar: Mapping[str, Any], fill: str) -> float:
    key = "close" if fill == "signal_minute_close" else "open"
    return float(bar.get(key) or 0)
```

For `signal_minute_close`, execute a selected candidate immediately using the current bar close. For `next_minute_open`, retain the pending-order path. For exits, call the same close helper immediately when the signal-minute mode is selected; otherwise queue the symbol for the next minute open. Keep T+1 by continuing to skip exit-signal evaluation when `entry_date >= timestamp.date()`.

- [ ] **Step 4: Run fill tests and existing MA/risk tests**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py -k "minute_entry_fill or minute_exit_fill or failed_next_minute_buy or ma5_exit or risk_control" -q
```

Expected: PASS.

- [ ] **Step 5: Commit minute fill behavior**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "feat(backtest): honor opening volume minute fill rules"
```

### Task 3: Suspension and one-price limit constraints

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`

- [ ] **Step 1: Write failing market-constraint tests**

Add tests using explicit one-price bars:

```python
def test_engine_rejects_one_price_limit_up_buy() -> None:
    result = _market_constraint_result(
        execution_bar={"open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0},
        previous_close=10.0,
    )
    assert result["trades"] == []
    assert result["execution"]["buy_limit_up"] == 1


def test_engine_keeps_limit_down_exit_pending_until_a_tradable_bar() -> None:
    result = _limit_down_then_recovery_result()
    assert result["trades"][0]["exit_datetime"] == "2026-01-07 09:30:00"
    assert result["execution"]["sell_limit_down"] == 1
```

Define both fixtures in the same test file:

```python
def _market_constraint_result(
    *,
    execution_bar: dict,
    previous_close: float,
) -> dict:
    symbol = "600000.SH"
    day = date(2026, 1, 5)
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "volume": 100.0,
            "previous_cumulative_volume": 10_000.0,
            **execution_bar,
        },
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": previous_close,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_closes": [9.0, 9.0, 9.0, 9.0],
    }}
    return MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(
            enable_branch_b=False,
            enable_branch_c=False,
        ),
    )).run(rows, contexts)


def _limit_down_then_recovery_result() -> dict:
    symbol = "600000.SH"
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": 10.1, "low": 10.0, "close": 10.0,
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 9, 30),
            "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0,
            "volume": 1_000.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 9, 31),
            "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0,
            "volume": 1_000.0, "previous_cumulative_volume": 200.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 7, 9, 30),
            "open": 9.5, "high": 9.6, "low": 9.4, "close": 9.5,
            "volume": 1_000.0, "previous_cumulative_volume": 100.0,
        },
    ]
    contexts = {
        (symbol, date(2026, 1, 5)): {
            "previous_open": 11.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": -0.02,
            "previous_closes": [9.0, 9.0, 9.0, 9.0],
        },
        (symbol, date(2026, 1, 6)): {
            "previous_open": 10.0, "previous_close": 10.0,
            "previous_high": 10.1, "previous_change_pct": 0.0,
            "previous_closes": [9.0, 9.0, 9.0, 9.0],
        },
        (symbol, date(2026, 1, 7)): {
            "previous_open": 9.0, "previous_close": 9.0,
            "previous_high": 9.0, "previous_change_pct": -0.10,
            "previous_closes": [9.0, 9.0, 9.0, 9.0],
        },
    }
    return MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], max_positions=1,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(stop_loss_pct=0.05),
    )).run(rows, contexts)
```

Test the extracted buy check directly for a missing execution bar:

```python
def test_missing_execution_bar_is_a_suspended_buy() -> None:
    assert _buy_block_reason(
        symbol="600000.SH",
        bar=None,
        previous_close=10.0,
        symbol_name="",
        fill="next_minute_open",
    ) == "buy_suspended"
```

- [ ] **Step 2: Run the market tests and verify RED**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py -k "limit_up_buy or limit_down or suspended" -q
```

Expected: FAIL because the dedicated minute engine does not identify one-price limits or return execution counters.

- [ ] **Step 3: Implement fixed market constraints**

Add helpers that use the unadjusted previous close already present in `daily_context`:

```python
def _price_limit_ratio(symbol: str, name: str) -> float:
    upper_name = name.upper()
    if upper_name.startswith(("ST", "*ST")):
        return 0.05
    board = _symbol_board(symbol)
    if board in {"科创板", "创业板"}:
        return 0.20
    if board == "北交所":
        return 0.30
    return 0.10


def _is_one_price_limit(
    symbol: str,
    bar: Mapping[str, Any],
    previous_close: float,
    name: str,
    direction: str,
) -> bool:
    prices = [float(bar.get(key) or 0) for key in ("open", "high", "low", "close")]
    if any(price <= 0 for price in prices):
        return False
    same_price = max(prices) - min(prices) <= max(abs(prices[-1]) * 1e-4, 0.01)
    ratio = _price_limit_ratio(symbol, name)
    multiplier = Decimal(str(1 + ratio if direction == "up" else 1 - ratio))
    limit_price = float(
        (Decimal(str(previous_close)) * multiplier).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )
    return same_price and abs(prices[-1] - limit_price) <= 0.01
```

Import `Decimal` and `ROUND_HALF_UP` from `decimal`. Use the helper before buy and sell execution. A missing execution bar is a suspended/absent buy and causes the one-minute buy order to expire. A blocked sell remains pending. Return execution counters for `buy_suspended`, `buy_limit_up`, `sell_suspended`, `sell_limit_down`, and invalid prices.

Expose the check as the private helper used by the engine and imported by its focused test:

```python
def _buy_block_reason(
    *,
    symbol: str,
    bar: Mapping[str, Any] | None,
    previous_close: float,
    symbol_name: str,
    fill: str,
) -> str | None:
    if bar is None:
        return "buy_suspended"
    if _entry_base_price(bar, fill) <= 0:
        return "buy_invalid_price"
    if _is_one_price_limit(symbol, bar, previous_close, symbol_name, "up"):
        return "buy_limit_up"
    return None
```

- [ ] **Step 4: Run market and legacy engine tests**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py -k "limit or suspended or fills_top or strict" -q
```

Expected: PASS, including the existing strict `>` breakout test.

- [ ] **Step 5: Commit market constraints**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py
git diff --cached --check
git commit -m "fix(backtest): enforce minute market trading constraints"
```

### Task 4: Forced liquidation and open-position valuation

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Write failing terminal-position tests**

Cover enabled, disabled, T+1, and blocked liquidation:

```python
def test_force_close_skips_new_entries_on_last_trading_day() -> None:
    result = _last_day_signal_result(force_close_at_end=True)
    assert result["trades"] == []
    assert result["open_positions"] == []


def test_disabled_force_close_returns_marked_open_position() -> None:
    result, _ = _overnight_position_result(force_close_at_end=False)
    assert result["trades"] == []
    assert result["open_positions"][0]["mark_price"] == 10.8
    assert result["final_equity"] == pytest.approx(
        result["cash"] + result["open_positions"][0]["market_value"],
    )


def test_force_close_cannot_bypass_final_limit_down() -> None:
    result, _ = _overnight_position_result(
        force_close_at_end=True,
        final_bar={"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0},
    )
    assert result["trades"] == []
    assert result["open_positions"][0]["exit_block_reason"] == "sell_limit_down"


def test_force_close_keeps_a_position_suspended_for_the_final_day() -> None:
    result, _ = _overnight_position_result(
        force_close_at_end=True,
        suspend_on_final_day=True,
    )
    assert result["trades"] == []
    assert result["open_positions"][0]["exit_block_reason"] == "sell_suspended"
```

Define the terminal fixtures explicitly:

```python
def _last_day_signal_result(*, force_close_at_end: bool) -> dict:
    symbol = "600000.SH"
    day = date(2026, 1, 5)
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.3, "high": 10.4, "low": 10.2, "close": 10.3,
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
        },
    ]
    contexts = {(symbol, day): {
        "previous_open": 11.0, "previous_close": 10.0,
        "previous_high": 10.5, "previous_change_pct": -0.02,
        "previous_closes": [9.0, 9.0, 9.0, 9.0],
    }}
    return MinutePortfolioEngine(MinutePortfolioConfig(
        symbols=[symbol], start=day, end=day, max_positions=1,
        force_close_at_end=force_close_at_end,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
    )).run(rows, contexts)


def _overnight_position_result(
    *,
    force_close_at_end: bool,
    final_bar: dict | None = None,
    suspend_on_final_day: bool = False,
) -> tuple[dict, MinutePortfolioConfig]:
    symbol = "600000.SH"
    dummy_symbol = "000001.SZ"
    start = date(2026, 1, 5)
    end = date(2026, 1, 6)
    last = final_bar or {"open": 10.7, "high": 10.9, "low": 10.6, "close": 10.8}
    rows = [
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 30),
            "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.2,
            "volume": 150.0, "previous_cumulative_volume": 100.0,
        },
        {
            "symbol": symbol, "datetime": datetime(2026, 1, 5, 9, 31),
            "open": 10.0, "high": 10.1, "low": 10.0, "close": 10.0,
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
        },
        ({
            "symbol": dummy_symbol, "datetime": datetime(2026, 1, 6, 15, 0),
            "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
        } if suspend_on_final_day else {
            "symbol": symbol, "datetime": datetime(2026, 1, 6, 15, 0),
            "volume": 10_000.0, "previous_cumulative_volume": 10_000.0,
            **last,
        }),
    ]
    contexts = {
        (symbol, start): {
            "previous_open": 11.0, "previous_close": 10.0,
            "previous_high": 10.5, "previous_change_pct": -0.02,
            "previous_closes": [9.0, 9.0, 9.0, 9.0],
        },
        (symbol, end): {
            "previous_open": 10.0, "previous_close": 10.0,
            "previous_high": 10.1, "previous_change_pct": 0.0,
            "previous_closes": [1.0, 1.0, 1.0, 1.0],
        },
    }
    config = MinutePortfolioConfig(
        symbols=[symbol, dummy_symbol] if suspend_on_final_day else [symbol],
        start=start, end=end,
        initial_capital=100_000.0, max_positions=1,
        force_close_at_end=force_close_at_end,
        commission_pct=0.0, stamp_tax_pct=0.0, slippage_bps=0.0,
        strategy_params=OpeningVolumeStrategyParams(stop_loss_pct=0.0),
    )
    return MinutePortfolioEngine(config).run(rows, contexts), config
```

Destructure the returned tuple in the two tests:

```python
result, config = _overnight_position_result(force_close_at_end=False)
result, config = _overnight_position_result(
    force_close_at_end=True,
    final_bar={"open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0},
)
```

Add a service-stat test asserting open positions change final equity but not completed-trade metrics:

```python
def test_open_positions_affect_equity_not_completed_trade_stats() -> None:
    executed, config = _overnight_position_result(force_close_at_end=False)
    stats = MinutePortfolioService._stats(executed, config)
    assert stats["final_equity"] == executed["final_equity"]
    assert stats["n_trades"] == 0
    assert stats["win_rate"] == 0.0
```

- [ ] **Step 2: Run terminal tests and verify RED**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py -k "force_close or open_position or final_equity" -q
```

Expected: FAIL because the engine always appends a terminal trade, clears positions, and calculates statistics from cash only.

- [ ] **Step 3: Implement terminal behavior**

Add `force_close_at_end: bool = True` to `MinutePortfolioConfig`. In real backtest configurations, where `start` and `end` are set, skip candidate generation on `trade_dates[-1]` when the flag is true. Keep pure engine fixtures with no configured range backward-compatible so the existing single-day rule tests still isolate entry behavior. After the loop, attempt liquidation only for overnight positions that have a valid bar on the final trading day and are not blocked by a one-price limit down.

Use this explicit guard around candidate generation:

```python
allow_new_entries = not (
    self.config.force_close_at_end
    and self.config.end is not None
    and timestamp.date() == trade_dates[-1]
)
```

Serialize remaining positions:

```python
open_positions.append({
    "symbol": symbol,
    "entry_datetime": position["entry_datetime"].isoformat(sep=" "),
    "entry_date": str(position["entry_date"]),
    "entry_price": round(float(position["entry_price"]), 4),
    "shares": int(position["shares"]),
    "mark_datetime": mark_timestamp.isoformat(sep=" "),
    "mark_date": str(mark_timestamp.date()),
    "mark_price": round(mark_price, 4),
    "market_value": round(position["shares"] * mark_price, 2),
    "unrealized_pnl_amount": round(market_value - position["entry_cost"], 2),
    "unrealized_pnl_pct": round(market_value / position["entry_cost"] - 1.0, 6),
    "exit_block_reason": block_reason,
})
```

Return `cash`, `final_equity`, `trades`, `open_positions`, `execution`, and the curves. Update `MinutePortfolioService._stats` to use `final_equity` while continuing to derive trade count and win rate only from `trades`. Add names to open positions and expose them in the service result. Extend `StrategyBacktestResult` with an optional typed `open_positions` array.

- [ ] **Step 4: Run terminal and statistics tests**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py -k "force_close or open_position or final_equity or returns_net_trade_pnl" -q
```

Expected: PASS.

- [ ] **Step 5: Commit terminal valuation**

```powershell
git add backend/app/backtest/minute_portfolio.py backend/tests/backtest/test_minute_portfolio.py frontend/src/lib/api.ts
git diff --cached --check
git commit -m "feat(backtest): report opening volume terminal positions"
```

### Task 5: Dedicated API propagation and validation

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio_api.py`
- Modify: `backend/app/api/backtest.py`

- [ ] **Step 1: Write failing API tests**

Extend the signature test:

```python
def test_minute_portfolio_stream_accepts_execution_controls() -> None:
    parameters = signature(backtest.minute_portfolio_stream).parameters
    assert {"candidate_sort", "entry_fill", "exit_fill", "force_close_at_end"} <= set(parameters)
```

In the existing `test_minute_portfolio_stream_builds_advanced_config_and_watchlist_subset`, add these arguments to its endpoint call:

```python
    response = await backtest.minute_portfolio_stream(
        request,
        start="2026-01-05",
        end="2026-01-06",
        candidate_sort="score",
        entry_fill="signal_minute_close",
        exit_fill="next_minute_open",
        force_close_at_end=False,
        symbols="000001.SZ,600000.SH,999999.SH",
        max_exposure_pct=0.97,
        params='{"stop_loss_pct": 0.025}',
        overrides='{"scoring":{"volume_ratio":0.7,"today_return":0.3}}',
    )

    config = captured["config"]
    assert config.candidate_sort == "score"
    assert config.entry_fill == "signal_minute_close"
    assert config.exit_fill == "next_minute_open"
    assert config.force_close_at_end is False
```

Keep all existing arguments and assertions in that test; the shorter override above only shows the new additions. Add explicit invalid-value coverage:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        {"candidate_sort": "unknown"},
        {"entry_fill": "close_t"},
        {"exit_fill": "open_t+1"},
    ],
)
async def test_minute_portfolio_stream_rejects_invalid_execution_controls(
    monkeypatch,
    tmp_path,
    values: dict,
) -> None:
    monkeypatch.setattr(
        "app.services.watchlist.list_symbols",
        lambda: [{"symbol": "600000.SH"}],
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            repo=SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path)),
            strategy_engine=None,
        )),
    )
    with pytest.raises(HTTPException) as exc_info:
        await backtest.minute_portfolio_stream(
            request,
            start="2026-01-05",
            end="2026-01-06",
            **values,
        )
    assert exc_info.value.status_code == 400
```

In `test_minute_portfolio_stream_passes_user_capital_and_positions`, assert the compatibility defaults on `captured["config"]`: `candidate_sort == "volume_ratio"`, both fills equal `"next_minute_open"`, and `force_close_at_end is True`.

- [ ] **Step 2: Run API tests and verify RED**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio_api.py -k "execution_controls or invalid_execution or legacy_execution" -q
```

Expected: FAIL because the endpoint signature and configuration omit these values.

- [ ] **Step 3: Add validated endpoint fields**

Add query parameters:

```python
candidate_sort: str = "volume_ratio",
entry_fill: str = "next_minute_open",
exit_fill: str = "next_minute_open",
force_close_at_end: bool = True,
```

Reject values outside `CANDIDATE_SORT_MODES` and `{"signal_minute_close", "next_minute_open"}` with HTTP 400. Pass all fields into `MinutePortfolioConfig`, include them in the job-key raw string, and echo them in result `config`. Do not add `max_buy_volume_ratio` to the endpoint.

- [ ] **Step 4: Run all dedicated API tests**

Run:

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit API propagation**

```powershell
git add backend/app/api/backtest.py backend/tests/backtest/test_minute_portfolio_api.py
git diff --cached --check
git commit -m "feat(backtest): expose minute execution controls"
```

### Task 6: Backtest-page controls and result display

**Files:**
- Modify: `frontend/src/pages/backtest/openingVolumeSettings.test.ts`
- Modify: `frontend/src/lib/backtestTask.ts`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`

- [ ] **Step 1: Write failing request and UI tests**

Move the existing SSE doubles into this shared helper at the top of the test file, and call it from both request tests:

```typescript
let openedUrl = ''

function installSseTestDoubles() {
  openedUrl = ''
  const storage = new Map<string, string>()
  ;(globalThis as any).localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  }
  ;(globalThis as any).EventSource = class {
    onopen = null
    onerror = null

    constructor(url: string) {
      openedUrl = url
    }

    addEventListener() {}
    close() {}
  }
}
```

Add the request serialization test:

```typescript
test('minute-portfolio requests serialize execution controls', () => {
  installSseTestDoubles()
  startBacktest({
    strategy_id: 'opening_volume_portfolio',
    engine: 'minute_portfolio',
    candidate_sort: 'score',
    entry_fill: 'signal_minute_close',
    exit_fill: 'next_minute_open',
    force_close_at_end: false,
  })

  const query = new URL(openedUrl, 'http://localhost').searchParams
  assert.equal(query.get('candidate_sort'), 'score')
  assert.equal(query.get('entry_fill'), 'signal_minute_close')
  assert.equal(query.get('exit_fill'), 'next_minute_open')
  assert.equal(query.get('force_close_at_end'), 'false')
})
```

Update the existing stock-range request test to call `installSseTestDoubles()` and remove its local `openedUrl`, storage, and `EventSource` declarations. Add source assertions for:

```typescript
assert.match(source, /候选排序方式/)
assert.match(source, /同期量比优先/)
assert.match(source, /股票池顺序/)
assert.match(source, /回测末期强制平仓/)
assert.match(source, /下一分钟开盘（推荐）/)
assert.match(source, /信号分钟收盘/)
assert.match(source, /单只目标金额 = 当前总资产 × 最大总仓位 ÷ 最大持仓数/)
assert.match(source, /期末未平仓/)
```

Assert the early-session branch hides the `评分加权` choice while non-minute strategies retain it.

- [ ] **Step 2: Run frontend tests and verify RED**

Run from `frontend`:

```powershell
node --test src/pages/backtest/openingVolumeSettings.test.ts
```

Expected: FAIL because request types, query fields, controls, labels, and open-position UI are absent.

- [ ] **Step 3: Serialize the new request fields**

Extend `startBacktest` parameters and `buildQuery`:

```typescript
candidate_sort?: 'score' | 'volume_ratio' | 'watchlist_order'
force_close_at_end?: boolean
```

For the dedicated minute path, pass the two internal fill values unchanged. Keep the generic strategy path using `close_t` and `open_t+1`.

- [ ] **Step 4: Add strategy-specific state and controls**

Persist state alongside the existing page settings:

```typescript
const [candidateSort, setCandidateSort] = useState<'score' | 'volume_ratio' | 'watchlist_order'>(
  saved?.candidateSort ?? 'volume_ratio',
)
const [forceCloseAtEnd, setForceCloseAtEnd] = useState(saved?.forceCloseAtEnd ?? true)
```

In the scoring tab, add the sort selector above the weights. Disable only the weight-edit controls when `candidateSort !== 'score'`; keep saved weights intact. In the risk tab, add the forced-liquidation checkbox with the T+1 and blocked-liquidation explanation.

Render existing fill selectors conditionally:

```tsx
{minuteNative ? (
  <>
    <option value="open_t+1">下一分钟开盘（推荐）</option>
    <option value="close_t">信号分钟收盘</option>
  </>
) : (
  <>
    <option value="open_t+1">次日开盘（推荐）</option>
    <option value="close_t">信号日收盘</option>
  </>
)}
```

Map these existing UI values in `handleRun`:

```typescript
entry_fill: minuteNative
  ? entryFill === 'close_t' ? 'signal_minute_close' : 'next_minute_open'
  : entryFill,
exit_fill: minuteNative
  ? exitFill === 'close_t' ? 'signal_minute_close' : 'next_minute_open'
  : exitFill,
candidate_sort: minuteNative ? candidateSort : undefined,
force_close_at_end: minuteNative ? forceCloseAtEnd : undefined,
```

For `minuteNative`, replace the buy-weight selector with a read-only equal-weight explanation. Leave the generic strategy selector unchanged.

- [ ] **Step 5: Display terminal open positions**

When `result.open_positions` is non-empty, render a compact “期末未平仓” table above completed-trade tabs with symbol/name, shares, entry price/time, mark price/time, market value, unrealized P&L, and block reason. Do not merge these rows into `result.trades`, daily realized P&L, win rate, or per-symbol completed-trade statistics.

- [ ] **Step 6: Run frontend tests and build**

Run from `frontend`:

```powershell
node --test src/pages/backtest/openingVolumeSettings.test.ts
pnpm run build
```

Expected: test PASS and production build succeeds.

- [ ] **Step 7: Commit frontend controls**

```powershell
git add frontend/src/lib/backtestTask.ts frontend/src/pages/backtest/StrategyBacktest.tsx frontend/src/pages/backtest/openingVolumeSettings.test.ts
git diff --cached --check
git commit -m "feat(backtest): add opening volume execution controls UI"
```

### Task 7: Full regression and delivery audit

**Files:**
- Verify only; modify a task-owned file only if a test exposes a defect in this plan's behavior.

- [ ] **Step 1: Run full focused backend suites**

```powershell
python -m pytest backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py backend/tests/test_opening_volume_strategy.py backend/tests/test_strategy_detail_signals.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend regression and build**

From `frontend`:

```powershell
node --test src/pages/backtest/openingVolumeSettings.test.ts
pnpm run build
```

Expected: PASS.

- [ ] **Step 3: Audit diffs without disturbing unrelated work**

```powershell
git diff --check
git status --short
git log --oneline -8
```

Confirm every task commit contains only the explicitly listed files. Do not stage `.planning/`, `.tmp/`, debug scripts, local data, or pre-existing unrelated modifications.

- [ ] **Step 4: Record compatibility and rollback**

In the delivery summary, state:

- Old requests default to volume-ratio ordering and next-minute-open entry/exit. Requests without the new forced-liquidation field retain last-day entries and the legacy final-minute liquidation behavior; the new UI explicitly sends its forced-liquidation choice.
- The new engine behavior blocks final-day entries when forced liquidation is enabled.
- Open positions affect final equity but not completed-trade statistics.
- Rollback is performed with new `git revert` commits in reverse task order; do not rebase, amend shared commits, force-push, or hard-reset.
