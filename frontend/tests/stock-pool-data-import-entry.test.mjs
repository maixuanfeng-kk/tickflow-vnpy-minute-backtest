import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'

const stockPools = fs.readFileSync(new URL('../src/pages/StockPools.tsx', import.meta.url), 'utf8')
const api = fs.readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8')

test('股票池未就绪区提供所需数据导入入口', () => {
  assert.match(stockPools, /导入所需数据/)
  assert.match(stockPools, /StockPoolDataImportDialog/)
})

test('专业日K导入客户端暴露启动和状态接口', () => {
  assert.match(api, /dailyProImportStart/)
  assert.match(api, /dailyProImportStatus/)
})

test('数据就绪检查会持续显示导入进度', () => {
  assert.match(stockPools, /dailyProImportStatus/)
  assert.match(stockPools, /importJob\.progress/)
  assert.match(stockPools, /专业日K导入进度/)
  assert.match(stockPools, /\{importJob \? \(/)
})
