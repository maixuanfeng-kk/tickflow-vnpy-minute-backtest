# vn.py Minute CTA Backtest Design

> **已替代（2026-07-27）：** 本设计中的单标的分钟 CTA 路径不再是运行时能力；当前实现使用本地分钟 Parquet、股票池组合引擎和独立策略注册表。本文仅保留作历史审计参考。

> **已替代（2026-07-27）：** 本设计中的单标的分钟 CTA 路径不再是运行时能力；当前实现使用本地分钟 Parquet、股票池组合引擎和独立策略注册表。本文仅保留作历史审计参考。

## Goal

Add a dedicated vn.py-backed, single-stock minute CTA backtest to TickFlow without
changing the existing Matrix-based daily and portfolio backtest paths.

## Scope

- Input data is the existing `kline_minute` Parquet repository populated by TickFlow's
  normal minute-K sync pipeline.
- The initial exposed strategy is `minute_double_ma_volume`.
- A request selects `engine="vnpy"`, one symbol, a date range, and strategy
  parameters.
- The API streams normal progress events and returns a normalized result containing
  summary metrics, equity curve, and trades.
- The backtest page exposes the engine only when minute mode is selected.

## Architecture

```text
StrategyBacktest UI
  -> /api/backtest/vnpy/stream (SSE)
  -> VnpyMinuteBacktestService
  -> KlineRepository.get_minute_range() -> BarData
  -> LocalNextBarOpenEngine + MinuteDoubleMaVolumeStrategy
  -> normalized TickFlow result -> SSE/UI
```

The existing `StrategyBacktestService` remains the owner of Matrix backtests. The
new service only adapts vn.py objects and has no dependency on its execution loops.
`LocalNextBarOpenEngine` keeps the established A-share execution model: next-bar
open fills, one-lot minimum, and volume participation limits. The CTA strategy keeps
T+1, lot-size, buy cutoff, forced sell, stop-loss, and take-profit controls.

## API Contract

The new stream endpoint accepts one stock symbol, `start`, `end`,
`strategy_id="minute_double_ma_volume"`, optional CTA settings, initial capital,
commission rate, slippage, lot size, and maximum volume participation. It rejects
unknown strategies, missing local data, invalid date ranges, or more than one symbol.

The final event uses the current strategy-backtest result shape where fields have the
same meaning. vn.py-specific metadata is placed under `config`: `engine="vnpy"`,
`frequency="1m"`, and `strategy_id`.

## UI

The strategy-backtest page adds an engine selector that is visible only for 1-minute
mode. Choosing vn.py constrains the symbol selection to one symbol and exposes only
the settings supported by the initial CTA. Existing daily and Matrix 1-minute paths
retain their current request shape and controls.

## Dependencies and Packaging

`vnpy` and `vnpy-ctastrategy` are optional backend dependencies. Development and
production installation include them only when the vn.py backtest feature is enabled.
The project uses the local `D:\quant\vnpy` checkout as the compatibility reference
(vn.py 4.4.0); it does not vendor that repository into TickFlow.

## Validation

- Unit-test CSV-to-BarData conversion and next-bar-open fills.
- Unit-test CTA signal, T+1, lot-size, cutoff, and forced-exit behavior.
- API-test valid SSE execution, invalid multi-symbol input, and missing CSV data.
- Build the frontend and run the affected backend tests.

## Non-goals

- Replacing the Matrix engine.
- Multi-stock vn.py portfolio simulation.
- Arbitrary uploaded Python strategy code.
- Live trading or broker connectivity.
