# Data Card Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the local minute-K overview card and show the local financial-import dataset as a matching data overview card.

**Architecture:** Keep the existing `StatCard` and financial-import panel. Update the data-card settings defaults and migrate legacy saved defaults so that locally stored minute and financial data are visible without a paid API capability. The data page reads the existing financial-import status query to populate the financial card.

**Tech Stack:** React 18, TypeScript, TanStack Query, Node test runner.

---

### Task 1: Preserve local-data cards through the legacy settings migration

**Files:**
- Create: `frontend/tests/data-card-visibility.test.mjs`
- Create: `frontend/src/lib/data-card-visibility.ts`
- Modify: `frontend/src/lib/storage.ts`
- Modify: `frontend/src/components/data/PageSettingsModal.tsx`

- [ ] **Step 1: Write a failing test**

```js
test('legacy card settings reveal locally imported minute and financial data', () => {
  assert.deepEqual(
    migrateLocalDataCardVisibility({ minute: false, financials: false }, 1),
    { minute: true, financials: true },
  )
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --experimental-strip-types --test tests/data-card-visibility.test.mjs`

Expected: FAIL because `migrateLocalDataCardVisibility` does not yet exist.

- [ ] **Step 3: Write the minimal migration and connect it to the settings helper**

```ts
export function migrateLocalDataCardVisibility(
  saved: Record<string, boolean>,
  version: number,
): Record<string, boolean> {
  if (version >= 2) return saved
  return { ...saved, minute: true, financials: true }
}
```

Persist version `2` after applying the migration. Make minute and financial cards visible by default and make the reset action preserve that default.

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --experimental-strip-types --test tests/data-card-visibility.test.mjs`

Expected: PASS.

### Task 2: Render local minute and financial datasets as overview cards

**Files:**
- Modify: `frontend/src/components/data/StatCard.tsx`
- Modify: `frontend/src/pages/Data.tsx`

- [ ] **Step 1: Read financial import status on the data page**

Use `QK.financialImportStatus` and `api.financialImportStatus` already used by `FinancialImportPanel`.

- [ ] **Step 2: Present the cards as local data**

Pass local-data state to `StatCard` for minute K when local trading days exist, and for the manually imported financial dataset. Build financial card stats from `dataset.rows`, `dataset.symbols`, `dataset.latest_report_date`, and `dataset.latest_publish_date`.

- [ ] **Step 3: Verify compile and production build**

Run: `pnpm exec tsc -b` and `pnpm exec vite build` from `frontend`.

Expected: both commands exit 0.
