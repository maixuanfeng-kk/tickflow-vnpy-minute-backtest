import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const stockPoolsPage = await readFile(new URL('../src/pages/StockPools.tsx', import.meta.url), 'utf8')

test('stock pool page limits screening to the May and June manual templates', () => {
  assert.match(stockPoolsPage, /month:2026-05/)
  assert.match(stockPoolsPage, /month:2026-06/)
  assert.doesNotMatch(stockPoolsPage, /type="month"/)
})

test('stock pool page saves a screening snapshot without publishing to Watchlist', () => {
  assert.match(stockPoolsPage, /保存筛选快照/)
  assert.doesNotMatch(stockPoolsPage, /保存并发布/)
  assert.doesNotMatch(stockPoolsPage, /invalidateQueries\(\{ queryKey: QK\.watchlistPools \}\)/)
})
