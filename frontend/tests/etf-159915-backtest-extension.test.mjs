import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ETF_159915_EXTENSION,
  buildEtf159915ExecutionRows,
  isEtf159915RunBlocked,
  resolveBacktestExtension,
} from '../src/pages/backtest/strategy-extensions/etf159915.ts'


test('only the 159915 strategy resolves the ETF extension', () => {
  assert.equal(resolveBacktestExtension('etf_159915_minute'), ETF_159915_EXTENSION)
  assert.equal(resolveBacktestExtension('opening_breakout_pool'), null)
  assert.deepEqual(ETF_159915_EXTENSION.entryRules, [
    '4.1.1', '4.1.2', '4.2.1', '4.2.2', '4.2.3-1', '4.2.3-2',
    'tail_1', 'tail_2', 'tail_3',
  ])
  assert.deepEqual(ETF_159915_EXTENSION.exitRules, [
    '5.1.1', '5.1.2', '5.1.3', '5.2', '5.3', '5.4', '5.4_rebuy', '5.5', '5.6',
  ])
})


test('ETF runs stay blocked while readiness is unavailable or not ready', () => {
  assert.equal(isEtf159915RunBlocked({ isLoading: true, isError: false }), true)
  assert.equal(isEtf159915RunBlocked({ isLoading: false, isError: true }), true)
  assert.equal(
    isEtf159915RunBlocked({
      isLoading: false,
      isError: false,
      data: { ready: false },
    }),
    true,
  )
  assert.equal(
    isEtf159915RunBlocked({
      isLoading: false,
      isError: false,
      data: { ready: true },
    }),
    false,
  )
})


test('execution rows join each signal to its fill by signal id', () => {
  const signals = [
    {
      id: 7,
      symbol: '159915.SZ',
      direction: 'SHORT',
      timestamp: '2025-01-10 14:13:00',
      reason: '5.1.2',
      conditions: ['5.1.2'],
      status: 'filled',
      due_at: '2025-01-10 14:14:00',
      fill_datetime: '2025-01-10 14:14:00',
    },
    {
      id: 8,
      symbol: '159915.SZ',
      direction: 'LONG',
      timestamp: '2025-01-14 09:52:00',
      reason: '4.1.1',
      conditions: ['4.1.1'],
      status: 'rejected',
      due_at: '2025-01-14 09:53:00',
      rejection_reason: 'insufficient_cash',
    },
  ]
  const fills = [
    {
      symbol: '159915.SZ',
      entry_date: '2025-01-10 14:14:00',
      exit_date: '',
      entry_price: 1.9548045,
      exit_price: 0,
      pnl_pct: 0,
      duration: 0,
      exit_reason: '5.1.2',
      direction: 'SHORT',
      shares: 4_930_800,
      commission: 1_156.65,
      slippage: 963.97,
      signal_id: 7,
    },
  ]

  const rows = buildEtf159915ExecutionRows(signals, fills)

  assert.equal(rows[0].fillPrice, 1.9548045)
  assert.equal(rows[0].shares, 4_930_800)
  assert.equal(rows[0].commission, 1_156.65)
  assert.equal(rows[0].slippage, 963.97)
  assert.equal(rows[1].fillPrice, null)
  assert.equal(rows[1].rejectionReason, 'insufficient_cash')
})
