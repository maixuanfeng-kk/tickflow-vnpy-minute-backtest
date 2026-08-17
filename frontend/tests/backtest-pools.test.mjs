import assert from 'node:assert/strict'
import test from 'node:test'

import {
  canRunBacktest,
  is159915Strategy,
  is159915StockPoolStrategy,
  normalizeBacktestSymbols,
  resolveBacktestSymbols,
  symbolsFromPoolEntries,
} from '../src/lib/backtest-pools.ts'

test('159915 strategy is identified as a single ETF strategy', () => {
  assert.equal(is159915Strategy('etf_159915_minute'), true)
  assert.equal(is159915Strategy('opening_breakout_pool'), false)
})

test('ETF timed stock pool strategy is distinct from the single ETF strategy', () => {
  assert.equal(is159915StockPoolStrategy('etf_159915_stock_pool'), true)
  assert.equal(is159915StockPoolStrategy('etf_159915_minute'), false)
})

test('pool members replace manual symbols and preserve exchange suffixes', () => {
  assert.deepEqual(
    resolveBacktestSymbols({
      source: 'pool',
      poolSymbols: ['000001.SZ', '600000.SH'],
      manualSymbols: ['300750.SZ'],
    }),
    ['000001.SZ', '600000.SH'],
  )
})

test('manual six-digit symbols are normalized before a request', () => {
  assert.deepEqual(
    normalizeBacktestSymbols(['000001', '600000', '430047']),
    ['000001.SZ', '600000.SH', '430047.BJ'],
  )
})

test('an empty selected pool is not runnable', () => {
  assert.equal(canRunBacktest({ source: 'pool', poolSymbols: [] }), false)
})

test('watchlist API entries become normalized backtest symbols', () => {
  assert.deepEqual(
    symbolsFromPoolEntries([{ symbol: '600000.XSHG' }, { symbol: '000001.SZ' }]),
    ['600000.SH', '000001.SZ'],
  )
})
