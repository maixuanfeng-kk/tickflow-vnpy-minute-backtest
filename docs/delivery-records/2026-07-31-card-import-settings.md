# Delivery record: card settings import entrypoints

## Objective

Make the minute-K and financial-data card gear buttons the single local-path import entrypoint on the Data page.

## Branch and scope

- Feature branch: `feature/card-import-settings`
- Target repository and branch: `vnpy-origin/main`
- Changed areas: Data-page card settings and the focused structural regression test.

## Changes

- Remove the duplicate `本地数据导入` page section.
- Keep minute-K synchronization controls and add the existing minute-K local import panel to the same settings modal.
- Add the existing financial local import panel to the financial card's settings modal, alongside the existing turnover-rebuild control.
- Enable both card gear buttons even before data has been imported, so the local import workflow is reachable on an empty installation.

## Preserved behavior

- Minute-K API synchronization configuration and manual synchronization remain available from its gear modal.
- Financial turnover rebuilding remains available from its gear modal.
- Existing local import APIs, progress reporting, validation, and storage behavior are unchanged.

## Verification

```powershell
cd frontend
node --test tests/card-import-entrypoints.test.mjs tests/data-card-visibility.test.mjs tests/watchlist-text-import.test.mjs tests/opening-volume-execution.test.mjs
# 8 passed

pnpm exec tsc -b
pnpm exec vite build
# passed; existing Vite large-chunk advisory only
```

Manual validation at `http://127.0.0.1:3020/data` confirmed that both card gear buttons open their respective local-path input and `扫描并导入` action. The minute-K modal also retains its synchronization controls, and the page no longer renders a separate import section.

## Compatibility, risks, and rollback

- No API, data format, or locally imported data changes occur.
- Users now access imports through the card gear buttons rather than the removed duplicate page section.
- Roll back with `git revert -m 1 <main-merge-sha>`; this restores the standalone entrypoint without modifying local minute or financial data.

## Conflict record

No merge conflicts occurred. The financial modal combines the new import control with the pre-existing turnover-rebuild control so both behaviors remain available.

## Integration

- Feature commit: `1a6dbd0 fix(data): move local imports to card settings`
- Main merge commit: `fe7a10c merge: card settings import entrypoints`
- The verification commands in this record were rerun from the merged `main` checkout before publication.
