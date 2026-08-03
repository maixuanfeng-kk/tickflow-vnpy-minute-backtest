# 交付记录：vn.py 开盘突破策略与前复权信号价

## 交付方式与例外

本次由任务负责人明确要求直接集成到 `main`。这是
`.codex/GIT_COLLABORATION_AND_DELIVERY_STANDARD.md` 中“常规不直接提交主干”规则的一次授权例外；其余要求仍执行：在最新远端 `main` 的干净克隆中开发、逐文件暂存、分组提交、验证后推送，并在推送后确认同步状态。

## 变更范围

标准 `kline_daily` 规范化保留 `pre_close`。新增管理员命令
`python -m app.scripts.build_local_adj_factor [--dry-run]`，从标准日线的“前一日原始收盘价 / 当日 pre_close”生成 `DATA_DIR/adj_factor/all.parquet`。构建器对缺失或无效的 `pre_close` 严格失败，不会静默混用原始和前复权价格。

vn.py 组合回测默认使用 `qfq` 信号价，信号判断、昨日价格比较和动态 MA 使用按交易日点时复权的价格；订单撮合、费用、滑点和成交量限制始终使用原始分钟行情。用户可在回测页显式选择 `raw`。当选择 `qfq` 时，任一请求标的/日期没有复权因子会明确失败。

`opening_volume_portfolio` 已替换为本地的完整开盘突破三条件策略，并提供 `opening_breakout_condition_1`、`opening_breakout_condition_2`、`opening_breakout_condition_3` 三个独立入口。四者固定使用 1.5 倍同期累计成交量；条件 1 使用前日阴线与盘中分钟最高价曾突破昨高，条件 2 使用当日涨幅大于 3% 且前两日涨幅小于 5%，条件 3 使用前日上涨且小于 3%。买入窗口、止损和动态 MA5 卖出沿用当前组合引擎行为。

`/vnpy/strategies`、`/vnpy/stream` 和取消任务键都携带 `strategy_id` 与 `signal_price_basis`。同一回测页会列出四个 vn.py 策略，且仅允许手工粘贴 1–1000 只股票代码；不会读取、提交或依赖股票池筛选、财务、XBX/Pro 日线模块。主干已有的其他回测入口没有被删除或改写。

## 验证

在本交付干净克隆中执行：

```powershell
cd backend
python -m pytest tests/vnpy_backtest/test_signal_prices.py tests/vnpy_backtest/test_service.py tests/vnpy_backtest/test_portfolio_framework.py tests/vnpy_backtest/test_api.py -q

cd ..\frontend
pnpm build
```

后端结果为 `38 passed`，仅有仓储中既存的 Polars `streaming` 弃用警告。前端类型检查与 Vite 生产构建均通过；Vite 仅报告既有的大包体积提示。测试覆盖日线 `pre_close` 保留、因子生成与缺失严格失败、qfq 投影、三条件边界、条件 1 不依赖前前日、策略注册、SSE 任务参数及取消任务键。

本次从最新远端 `main` 的干净克隆开始，未出现合并冲突；共享接口通过在现有路由与回测页中新增 vn.py 命名空间字段的方式保留原有行为。

## 排除项与回滚

未提交 `.env`、CSV/Parquet 数据、`outputs/`、缓存、构建产物、虚拟环境、股票池筛选模块及其 API/页面、财务或 XBX/Pro 日线导入。

如需回滚本交付，请在 `main` 按提交逆序执行 `git revert <提交SHA>`；三个提交均为线性提交，因此不需要 `-m` 参数。推送完成后的准确提交 SHA 以 `git log --oneline -3` 为准。
