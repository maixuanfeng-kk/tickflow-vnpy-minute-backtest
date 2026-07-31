import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const stockPoolsPage = await readFile(new URL('../src/pages/StockPools.tsx', import.meta.url), 'utf8')

test('stock pool page screens the full market for a chosen month', () => {
  assert.match(stockPoolsPage, /type="month"/)
  assert.match(stockPoolsPage, /全市场/)
  assert.doesNotMatch(stockPoolsPage, /SOURCE_TEMPLATES/)
})

test('stock pool page refreshes generated pools after save', () => {
  assert.match(stockPoolsPage, /保存并生成股票池/)
  assert.match(stockPoolsPage, /invalidateQueries\(\{ queryKey: QK\.watchlistPools \}\)/)
})
