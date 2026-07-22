# Opening Volume ABC Layout Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove redundant mandatory-rule switches and render opening-volume parameters as shared A/B/C branch cards in both settings surfaces.

**Architecture:** The backend strategy schema exposes only editable values and the runtime enforces branch volume plus the A breakout rule unconditionally. A focused React component owns the opening-volume layout and field rendering, while both existing pages keep their save/request state ownership.

**Tech Stack:** Python dataclasses and pytest; React 18, TypeScript, Tailwind CSS, Lucide icons, Vite.

---

### Task 1: Enforce mandatory strategy rules

**Files:**
- Modify: `backend/tests/backtest/test_minute_portfolio.py`
- Modify: `backend/app/backtest/minute_portfolio.py`

- [ ] **Step 1: Write failing compatibility tests**

Add a test that constructs parameters from legacy disabled switches and proves low volume still blocks A/B/C and missing previous-high breakout still blocks A.

```python
params = OpeningVolumeStrategyParams.from_mapping({
    "enable_branch_a_volume_filter": False,
    "enable_branch_b_volume_filter": False,
    "enable_branch_c_volume_filter": False,
    "branch_a_require_previous_high_breakout": False,
})
```

- [ ] **Step 2: Run the focused test and verify RED**

Run from `backend`: `uv run --extra dev pytest tests/backtest/test_minute_portfolio.py -q`

Expected: the compatibility assertions fail because the current runtime still honors the disabled switches.

- [ ] **Step 3: Remove the redundant runtime switches**

Delete the four fields and parsing branches. Simplify the volume predicate so every enabled branch always compares its branch-specific value, then require `crossed_previous_high` directly in Branch A.

```python
def _passes_volume_filter(
    *, branch_multiple: float | None, legacy_multiple: float | None, volume_ratio: float,
) -> bool:
    required_multiple = branch_multiple or legacy_multiple or VOLUME_RATIO_MIN
    return volume_ratio >= required_multiple
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run from `backend`: `uv run --extra dev pytest tests/backtest/test_minute_portfolio.py -q`

Expected: PASS.

### Task 2: Correct the public parameter schema

**Files:**
- Modify: `backend/tests/test_opening_volume_strategy.py`
- Modify: `backend/app/strategy/builtin/opening_volume_portfolio.py`

- [ ] **Step 1: Write a failing metadata assertion**

Update the exact defaults assertion so it contains 16 editable fields and excludes all three volume switches plus `branch_a_require_previous_high_breakout`.

- [ ] **Step 2: Run the metadata test and verify RED**

Run from `backend`: `uv run --extra dev pytest tests/test_opening_volume_strategy.py -q`

Expected: FAIL because the four redundant fields are still present.

- [ ] **Step 3: Remove the four fields from metadata**

Keep branch enable switches and branch volume multiples. Do not add display-only pseudo-parameters for the fixed A rule.

- [ ] **Step 4: Run the metadata test and verify GREEN**

Run from `backend`: `uv run --extra dev pytest tests/test_opening_volume_strategy.py -q`

Expected: PASS.

### Task 3: Build the shared branch-card editor

**Files:**
- Create: `frontend/src/components/strategy/OpeningVolumeParamsEditor.tsx`

- [ ] **Step 1: Create the focused component**

Define this public contract:

```tsx
interface OpeningVolumeParamsEditorProps {
  definitions: StrategyParamDef[]
  values: Record<string, any>
  onChange: (id: string, value: any) => void
}
```

Render an unframed common-settings band and a responsive three-card branch grid. Branch A includes a read-only `必须突破昨日最高价` rule row. A disabled branch dims its editable body without clearing values.

- [ ] **Step 2: Compile the component**

Run from `frontend`: `pnpm build`

Expected: exit code 0.

### Task 4: Use the shared editor on both pages

**Files:**
- Modify: `frontend/src/components/screener/StrategySettingsDialog.tsx`
- Modify: `frontend/src/pages/backtest/StrategyBacktest.tsx`

- [ ] **Step 1: Replace the strategy-dialog flat list**

When `detail.id === 'opening_volume_portfolio'`, render `OpeningVolumeParamsEditor` directly and pass the existing `params` state setter. Leave every other strategy on `ParamField`.

- [ ] **Step 2: Replace the backtest flat list**

When the same strategy is selected, render the shared editor directly and update `strategyParams`. Leave every other strategy on `StrategyParamInput`.

- [ ] **Step 3: Build both integrations**

Run from `frontend`: `pnpm build`

Expected: exit code 0.

### Task 5: Regression and browser verification

**Files:**
- Test all files above.

- [ ] **Step 1: Run focused backend regression**

Run from `backend`: `uv run --extra dev pytest tests/backtest/test_minute_portfolio.py tests/backtest/test_minute_portfolio_api.py tests/test_opening_volume_strategy.py -q`

Expected: PASS.

- [ ] **Step 2: Run frontend production build**

Run from `frontend`: `pnpm build`

Expected: exit code 0.

- [ ] **Step 3: Verify both live UI surfaces**

Open the strategy settings page and backtest advanced settings. Confirm three branch cards, 16 parameters, no volume-condition switches, one fixed A breakout row, preserved branch values after disable/re-enable, and no console errors.

- [ ] **Step 4: Inspect scope and commit**

Run `git diff --check`, stage only the backend/runtime tests, metadata, shared component, and two integration files, then commit with `fix(strategy): simplify opening volume branch settings`.
