# 早盘放量组合参数 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 让早盘放量组合的初始资金和最大持仓采用策略默认值，并允许用户在回测页修改后实际生效。

**Architecture:** MinutePortfolioConfig 保持策略默认值 1000 万和 8 仓；专用 SSE 接口接收并校验用户覆盖值，结果回显实际配置。前端选中早盘放量组合时只初始化这两个通用输入，并把当前输入值发送给专用接口。

**Tech Stack:** FastAPI SSE、pytest、React、TypeScript、Vite。

---

### Task 1: 让分钟组合服务回显实际资金和持仓参数

**Files:**
- Modify: backend/app/backtest/minute_portfolio.py
- Test: backend/tests/backtest/test_minute_portfolio.py

- [ ] **Step 1: 写失败测试**

在 test_service_reads_daily_and_minute_rows_and_returns_backtest_shape 中创建配置时传入 initial_capital=2_000_000.0, max_positions=4，并增加断言：

    assert result["config"]["initial_capital"] == 2_000_000.0
    assert result["config"]["max_positions"] == 4

- [ ] **Step 2: 验证测试失败**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py::test_service_reads_daily_and_minute_rows_and_returns_backtest_shape -q

Expected: FAIL，因为结果 config 尚未包含 max_positions。

- [ ] **Step 3: 最小实现**

在 MinutePortfolioService.run() 的结果 config 中新增：

    "max_positions": config.max_positions,

保持 MinutePortfolioEngine 已有的 config.initial_capital / config.max_positions 单仓目标金额计算不变。

- [ ] **Step 4: 验证通过**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py -q

Expected: PASS。

### Task 2: 让分钟组合 SSE 接口接收用户覆盖值

**Files:**
- Modify: backend/app/api/backtest.py
- Create: backend/tests/backtest/test_minute_portfolio_api.py

- [ ] **Step 1: 写失败的 API 配置传递测试**

用 monkeypatch 替换 watchlist.list_symbols 和 MinutePortfolioService，调用 minute_portfolio_stream() 时传入 initial_capital=2_000_000、max_positions=4，断言服务收到的 MinutePortfolioConfig 对应字段分别为 2_000_000.0 和 4。

- [ ] **Step 2: 验证测试失败**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio_api.py -q

Expected: FAIL，因为当前接口不接受这两个查询参数。

- [ ] **Step 3: 最小实现与校验**

为 minute_portfolio_stream() 增加参数：

    initial_capital: float = INITIAL_CAPITAL
    max_positions: int = MAX_POSITIONS

在构造配置前拒绝非正数，并传入 MinutePortfolioConfig：

    if initial_capital <= 0 or max_positions <= 0:
        raise HTTPException(status_code=400, detail="initial_capital and max_positions must be positive")

    initial_capital=initial_capital,
    max_positions=max_positions,

将两个值加入分钟组合任务键，避免不同参数复用同一 SSE 任务。

- [ ] **Step 4: 验证通过**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio_api.py backend/tests/backtest/test_minute_portfolio.py -q

Expected: PASS。

### Task 3: 在前端选中策略时应用策略默认值并传递当前输入

**Files:**
- Modify: frontend/src/pages/backtest/StrategyBacktest.tsx
- Modify: frontend/src/lib/backtestTask.ts

- [ ] **Step 1: 记录当前失败的 UI 行为**

在已启动的 http://127.0.0.1:3011/backtest 页面选择“早盘放量组合”，记录“初始资金”仍为 1000000、“最大持仓数”仍为 10。

- [ ] **Step 2: 最小实现**

在 StrategyBacktest.tsx 定义早盘放量组合的前端默认值：

    const MINUTE_PORTFOLIO_DEFAULTS = {
      initialCapital: '10000000',
      maxPositions: '8',
    } as const

新增单一的选择处理函数：选择早盘放量组合时，关闭 vn.py 分钟模式、写入上述两个默认值；选择普通策略时，关闭早盘放量模式。handleRun() 在早盘放量模式中继续使用当前 initialCapital 和 maxPositions，不再强制覆写为常量。

backtestTask.ts 已把这两个查询参数序列化；只保持分钟组合不传股票代码和路由选择逻辑。

- [ ] **Step 3: 构建验证**

Run: pnpm --dir frontend build

Expected: exit code 0。

- [ ] **Step 4: 浏览器验证**

刷新回测页，选择“早盘放量组合”，确认初始资金显示 10000000、最大持仓显示 8，两者仍可编辑。修改为 2000000 和 4 后运行，并确认结果 config 回显这两个值。

### Task 4: 回归与提交

**Files:**
- Modify only files from Tasks 1-3 if verification exposes a defect.

- [ ] **Step 1: 运行受影响后端测试**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py -q

Expected: PASS。

- [ ] **Step 2: 检查变更范围**

Run: git diff --check; git status --short

Expected: 无空白错误；不暂存既有的 frontend/vite.config.js、frontend/vite.config.d.ts 与 frontend/pnpm-workspace.yaml 修改。

- [ ] **Step 3: 提交功能改动**

    git add -- backend/app/backtest/minute_portfolio.py backend/app/api/backtest.py backend/tests/backtest/test_minute_portfolio.py backend/tests/backtest/test_minute_portfolio_api.py frontend/src/pages/backtest/StrategyBacktest.tsx frontend/src/lib/backtestTask.ts
    git commit -m "fix: honor opening volume portfolio parameters"
