# ETF 159915 Differences-Only Audit Workbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a one-sheet Excel workbook containing only the 23 discrepancies between the current TickFlow replay and `trade_log(1).csv`.

**Architecture:** Reuse the existing audit strategy to replay current local data and capture condition snapshots at both local and external timestamps. Normalize the comparison into one JSON row per discrepancy, then build and render a single `逐笔差异复盘` worksheet with `@oai/artifact-tool`.

**Tech Stack:** Python 3.12, current TickFlow vn.py backtest service, JSON, Node.js, `@oai/artifact-tool`.

---

### Task 1: Produce Reconciled Difference Data

**Files:**
- Create: `.tmp/etf_trade_differences/export_differences.py`
- Create: `.tmp/etf_trade_differences/differences.json`

- [ ] **Step 1: Create the extraction script**

Use `AuditEtf159915Strategy` from `.tmp/etf_trade_audit/audit_export.py`, run the current strategy for 2023-01-01 through 2025-12-31 with zero slippage, and load the user report from:

```text
C:/Users/Administrator/Documents/xwechat_files/wxid_l2759ee1uh6z22_6e42/msg/file/2026-08/trade_log(1).csv
```

Match in this order: exact time/direction/rule, same-day direction/rule, then same-day direction. Emit paired time/rule mismatches, reference-only rows, and local-only rows. Include audit conditions, market snapshots, state explanations, signal/fill details, and source paths.

- [ ] **Step 2: Run the extraction script**

Run:

```powershell
$env:PYTHONPATH='D:\quant\tickflow-stock-panel\backend;D:\quant\tickflow-stock-panel\.tmp\etf_trade_audit'
backend\.venv\Scripts\python.exe .tmp\etf_trade_differences\export_differences.py
```

Expected output:

```text
differences=23 time=9 rule=3 reference_only=4 local_only=7
```

- [ ] **Step 3: Validate the normalized data**

The script must assert all of the following before writing JSON:

```python
assert len(rows) == 23
assert Counter(row["category"] for row in rows) == {
    "时间差异": 9,
    "规则差异": 3,
    "外部独有": 4,
    "本地独有": 7,
}
```

### Task 2: Build and Verify the Workbook

**Files:**
- Create: `.tmp/etf_trade_differences/build_workbook.mjs`
- Create: `.tmp/etf_trade_differences/preview_top.png`
- Create: `.tmp/etf_trade_differences/preview_bottom.png`
- Create: `outputs/019ff381-0ed4-7d42-bb1b-eaa87e1f4db4/ETF159915_逐笔差异复盘_2023-2025.xlsx`

- [ ] **Step 1: Build the single worksheet**

Create `逐笔差异复盘` with title, source/method subtitle, a four-cell count strip, and a filterable detail table. Use real Excel datetimes and numeric prices/deltas. Preserve the reference workbook's navy/blue/pale/amber/red visual language, frozen panes, wrapped review text, and category highlighting.

- [ ] **Step 2: Inspect workbook values and formulas**

Use `workbook.inspect` on the title, count strip, headers, first discrepancy, and last discrepancy. Scan for `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, and `#N/A`.

- [ ] **Step 3: Render visual review images**

Render the top and bottom portions of `逐笔差异复盘`. Verify that all headers are readable, dates and prices use correct formats, long text wraps without overlap, category colors are distinct, and no content is clipped.

- [ ] **Step 4: Export the final workbook**

Export exactly one `.xlsx` file to the output path above and verify that it exists and is non-empty.
