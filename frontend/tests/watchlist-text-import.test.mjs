import assert from 'node:assert/strict'
import test from 'node:test'

import { parseWatchlistSymbols } from '../src/lib/watchlist-text-import.ts'

test('解析逗号、空格和换行分隔的股票代码并去重', () => {
  const result = parseWatchlistSymbols(
    '000021.SZ,000060.SZ\n000070.SZ 000100.SZ；000333.SZ,000021.SZ',
  )

  assert.deepEqual(result, [
    '000021.SZ',
    '000060.SZ',
    '000070.SZ',
    '000100.SZ',
    '000333.SZ',
  ])
})

test('保留合法交易所后缀并忽略无效文本', () => {
  const result = parseWatchlistSymbols('000429.SZ, 600000.SH, 430047.BJ, 错误代码, 12345.SZ')

  assert.deepEqual(result, ['000429.SZ', '600000.SH', '430047.BJ'])
})
