import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveOpeningVolumePool } from '../src/lib/openingVolumeExecution.ts'

test('开盘量回测优先使用手工股票池并去重', () => {
  const result = resolveOpeningVolumePool('000001.SZ, 600000.SH, 000001.SZ', ['300001.SZ'])

  assert.deepEqual(result, ['000001.SZ', '600000.SH'])
})

test('开盘量回测未指定手工股票池时回退到自选列表', () => {
  const result = resolveOpeningVolumePool('', ['000001.SZ', '000001.SZ', '600000.SH'])

  assert.deepEqual(result, ['000001.SZ', '600000.SH'])
})
