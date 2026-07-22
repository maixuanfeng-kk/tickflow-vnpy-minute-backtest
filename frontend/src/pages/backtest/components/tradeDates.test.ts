import assert from 'node:assert/strict'
import test from 'node:test'

import { addCalendarDays, resolveTradeDates } from './tradeDates.ts'

test('resolves replay dates from minute datetimes', () => {
  assert.deepEqual(resolveTradeDates({
    entry_datetime: '2026-01-05 09:31:00',
    exit_datetime: '2026-01-06 09:30:00',
  }), {
    entry: '2026-01-05',
    exit: '2026-01-06',
  })
})

test('prefers standard trade dates when both forms exist', () => {
  assert.deepEqual(resolveTradeDates({
    entry_date: '2026-02-02',
    exit_date: '2026-02-03',
    entry_datetime: '2026-01-05 09:31:00',
    exit_datetime: '2026-01-06 09:30:00',
  }), {
    entry: '2026-02-02',
    exit: '2026-02-03',
  })
})

test('rejects invalid trade dates without throwing', () => {
  assert.equal(resolveTradeDates({ entry_date: 'undefined', exit_date: '' }), null)
  assert.equal(resolveTradeDates({ entry_date: '2026-02-30', exit_date: '2026-03-01' }), null)
  assert.equal(addCalendarDays('undefined', -45), null)
})

test('adds calendar days to a valid ISO date', () => {
  assert.equal(addCalendarDays('2026-01-05', -45), '2025-11-21')
  assert.equal(addCalendarDays('2026-01-06', 20), '2026-01-26')
})
