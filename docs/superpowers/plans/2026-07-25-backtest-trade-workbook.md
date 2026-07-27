# Backtest Trade Workbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export every recorded buy/sell from the eight three-year backtest results into one verified, readable Excel workbook.

**Architecture:** A conversation-local JavaScript builder reads the eight result JSON files and the normalized-data manifest, maps symbols to Chinese names, and writes a summary, parameters, coverage, checks, and eight separate trade-detail sheets. The workbook is authored and rendered only with the bundled `@oai/artifact-tool` runtime.

**Tech Stack:** Bundled Node.js, `@oai/artifact-tool`, XLSX, JSON source results.

---

### Task 1: Audit workbook source data

**Files:**
- Read: `.tmp/three_year_backtest_20260724/results/*.json`
- Read: `.tmp/three_year_backtest_20260724/normalized/manifest.json`

- [ ] **Step 1: Validate all eight sources**

Parse `complete_66`, `available_77`, and A/B/C results for both universes. Assert trade arrays match reported `n_trades`, date bounds are 2023-07-17 through 2026-07-16, and capital plus rounded trade PnL reconciles to final equity within CNY 1.

- [ ] **Step 2: Build an auditable sheet map**

Map results to `组合77`, `组合66`, `A_77`, `B_77`, `C_77`, `A_66`, `B_66`, and `C_66`. Map symbol names and coverage states from the manifest.

### Task 2: Build the workbook

**Files:**
- Create: `.tmp/backtest_trade_workbook/build.mjs`
- Output: `outputs/019f936b-ca30-74e3-8bc0-640363b8da70/tickflow_three_year_trade_ledger.xlsx`

- [ ] **Step 1: Configure the bundled runtime**

Create a Windows junction from `.tmp/backtest_trade_workbook/node_modules` to the loader-provided Node packages. Use the loader-provided Node executable.

- [ ] **Step 2: Create workbook structure**

Add sheets in this order: `汇总`, `参数`, `数据覆盖`, `检查`, then the eight trade sheets. Hide gridlines and use a restrained dark-header/neutral-body/red-green exception style.

- [ ] **Step 3: Populate the summary and audit sheets**

Write typed totals, returns, annual returns, drawdowns, win rates, trade counts, initial/final equity, data-universe caveats, strategy parameters, and the 77-symbol coverage table. The checks sheet must compare source trade counts and PnL totals against detail-sheet formulas and show `OK`/`FAIL`.

- [ ] **Step 4: Populate all trade-detail sheets**

Use columns:

```text
序号, 股票代码, 股票名称, 入场分支, 买入时间, 买入价, 股数, 买入成本,
卖出时间, 卖出价, 持仓天数, 卖出原因, 盈亏金额, 收益率, 最大浮盈, 最大浮亏
```

Write dates as `Date`, prices/currency/counts/percentages as numeric cells, freeze the header row, add filters/tables, and apply conditional formatting to PnL/return/excursion columns.

- [ ] **Step 5: Export the workbook**

Export exactly one XLSX to the required output path.

### Task 3: Verify values, formulas, and visuals

**Files:**
- Inspect/render: generated workbook

- [ ] **Step 1: Inspect key ranges**

Inspect `汇总!A1:I14`, `检查!A1:H12`, `数据覆盖!A1:H20`, and representative detail ranges. Confirm the eight trade counts and metric values match source JSON.

- [ ] **Step 2: Scan formula errors**

Search the workbook for `#REF!|#DIV/0!|#VALUE!|#NAME?|#N/A`. Expected: zero matches.

- [ ] **Step 3: Render every sheet**

Render compact populated ranges from all 12 sheets, create a contact sheet for visual review, and fix clipped headers, excessive widths, unreadable wrapping, or overlapping content.

- [ ] **Step 4: Re-export and perform final reconciliation**

After any visual fixes, export once more, inspect summary/check ranges, and confirm all checks are `OK`.
