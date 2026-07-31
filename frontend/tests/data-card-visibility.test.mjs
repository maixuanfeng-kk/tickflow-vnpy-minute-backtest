import assert from 'node:assert/strict'
import test from 'node:test'

import { migrateLocalDataCardVisibility } from '../src/lib/data-card-visibility.ts'

test('legacy card settings reveal locally imported minute and financial data', () => {
  assert.deepEqual(
    migrateLocalDataCardVisibility({ minute: false, financials: false }, 1),
    { minute: true, financials: true },
  )
})

test('current card settings preserve a user-selected visibility state', () => {
  assert.deepEqual(
    migrateLocalDataCardVisibility({ minute: false, financials: false }, 2),
    { minute: false, financials: false },
  )
})
