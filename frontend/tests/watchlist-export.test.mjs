import assert from 'node:assert/strict'
import test from 'node:test'

import { buildWatchlistCsv, getWatchlistExportFilename } from '../src/lib/watchlist-export.ts'

test('exports watchlist entries as Excel-friendly CSV with escaped text', () => {
  const csv = buildWatchlistCsv([
    { symbol: '000001.SZ', name: '平安银行', note: '观察,回调后买入' },
    { symbol: '600000.SH', name: '浦发"银行', note: '' },
  ])

  assert.equal(
    csv,
    '\uFEFF代码,名称,备注\r\n000001.SZ,平安银行,"观察,回调后买入"\r\n600000.SH,"浦发""银行",\r\n',
  )
})

test('uses the active stock pool label in the download filename', () => {
  assert.equal(getWatchlistExportFilename('2026-05 条件选股结果'), '自选股_2026-05 条件选股结果.csv')
})
