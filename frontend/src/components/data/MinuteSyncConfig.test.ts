import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('minute sync settings expose local parquet import controls', () => {
  const source = readFileSync(new URL('./MinuteSyncConfig.tsx', import.meta.url), 'utf8')
  const apiSource = readFileSync(new URL('../../lib/api.ts', import.meta.url), 'utf8')

  assert.match(source, /本地分钟 Parquet 导入/)
  assert.match(source, /F:\\\\quant\\\\data\\\\minute_1min_pytdx/)
  assert.match(source, /api\.importLocalMinuteParquet\(localParquetDir\.trim\(\)\)/)
  assert.match(apiSource, /importLocalMinuteParquet:/)
})

test('minute K settings remain available before data is synchronized', () => {
  const source = readFileSync(new URL('../../pages/Data.tsx', import.meta.url), 'utf8')

  assert.match(
    source,
    /onSettings=\{\(\) => setOpenSettings\(v => v === 'minute' \? null : 'minute'\)\}/,
  )
})

test('minute K card is shown without TickFlow minute permission for local import', () => {
  const source = readFileSync(new URL('./PageSettingsModal.tsx', import.meta.url), 'utf8')

  assert.match(
    source,
    /\{ key: 'minute',[\s\S]*?defaultHiddenIfNoCap: false \}/,
  )
})
