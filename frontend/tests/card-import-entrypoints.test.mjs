import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const dataPage = await readFile(new URL('../src/pages/Data.tsx', import.meta.url), 'utf8')

test('minute K settings modal includes the local import entrypoint', () => {
  assert.match(
    dataPage,
    /\{openSettings === 'minute' && \((?:(?!<\/SettingsModal>)[\s\S])*?<MinuteImportPanel \/>/,
  )
})

test('financial settings modal includes the local import entrypoint', () => {
  assert.match(
    dataPage,
    /\{openSettings === 'financials' && \((?:(?!<\/SettingsModal>)[\s\S])*?<FinancialImportPanel \/>/,
  )
})
