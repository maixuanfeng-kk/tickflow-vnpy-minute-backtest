import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { startBacktest } from '../../lib/backtestTask.ts'

let openedUrl = ''

function installSseTestDoubles() {
  openedUrl = ''
  const storage = new Map<string, string>()
  ;(globalThis as any).localStorage = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  }
  ;(globalThis as any).EventSource = class {
    onopen = null
    onerror = null

    constructor(url: string) {
      openedUrl = url
    }

    addEventListener() {}
    close() {}
  }
}

test('opening-volume advanced settings expose exactly the five strategy tabs', () => {
  const source = readFileSync(new URL('./StrategyBacktest.tsx', import.meta.url), 'utf8')
  const openingTabs = source.match(
    /const OPENING_VOLUME_ADVANCED_TABS = ADVANCED_TABS\.filter\(tab => \(([\s\S]*?)\n\)\)/,
  )?.[1]

  assert.ok(openingTabs)
  for (const tab of ['params', 'filter', 'scoring', 'risk', 'range']) {
    assert.match(openingTabs, new RegExp(`['"]${tab}['"]`))
  }
  assert.doesNotMatch(openingTabs, /['"]entry['"]|['"]exit['"]/)
  assert.match(source, /openingVolumeStrategy\s*\?\s*OPENING_VOLUME_ADVANCED_TABS\s*:\s*minuteNative/)
})

test('minute-portfolio requests preserve an explicit stock range', () => {
  installSseTestDoubles()

  startBacktest({
    strategy_id: 'opening_volume_portfolio',
    symbols: ['600000.SH', '000001.SZ'],
    engine: 'minute_portfolio',
  })

  const query = new URL(openedUrl, 'http://localhost').searchParams
  assert.equal(query.get('symbols'), '600000.SH,000001.SZ')
})

test('minute-portfolio requests serialize execution controls', () => {
  installSseTestDoubles()
  startBacktest({
    strategy_id: 'opening_volume_portfolio',
    engine: 'minute_portfolio',
    candidate_sort: 'score',
    entry_fill: 'signal_minute_close',
    exit_fill: 'next_minute_open',
    force_close_at_end: false,
  })

  const query = new URL(openedUrl, 'http://localhost').searchParams
  assert.equal(query.get('candidate_sort'), 'score')
  assert.equal(query.get('entry_fill'), 'signal_minute_close')
  assert.equal(query.get('exit_fill'), 'next_minute_open')
  assert.equal(query.get('force_close_at_end'), 'false')
})

test('opening-volume fields keep risk and minute-supported filters unambiguous', () => {
  const backtestSource = readFileSync(new URL('./StrategyBacktest.tsx', import.meta.url), 'utf8')
  const editorSource = readFileSync(
    new URL('../../components/strategy/OpeningVolumeParamsEditor.tsx', import.meta.url),
    'utf8',
  )

  assert.match(backtestSource, /OPENING_VOLUME_FILTER_FIELDS/)
  assert.match(backtestSource, /openingVolumeStrategy\s*\?\s*Number\(strategyParams\.stop_loss_pct/)
  assert.match(backtestSource, /hideRiskFields/)
  assert.match(backtestSource, /候选排序方式/)
  assert.match(backtestSource, /同期量比优先/)
  assert.match(backtestSource, /股票池顺序/)
  assert.match(backtestSource, /回测末期强制平仓/)
  assert.match(backtestSource, /下一分钟开盘（推荐）/)
  assert.match(backtestSource, /信号分钟收盘/)
  assert.match(backtestSource, /单只目标金额 = 当前总资产 × 最大总仓位 ÷ 最大持仓数/)
  assert.match(backtestSource, /期末未平仓/)
  assert.match(backtestSource, /自选股子集/)
  assert.match(backtestSource, /emptyLabel="全部自选股"/)
  assert.match(backtestSource, /emptyDescription="默认使用全部 TickFlow 自选股。"/)
  assert.match(backtestSource, /仅在同一分钟候选超过剩余名额时进行评分和区间筛选/)
  assert.match(editorSource, /hideRiskFields/)
})

test('position backtest fee fields name their units explicitly', () => {
  const source = readFileSync(new URL('./StrategyBacktest.tsx', import.meta.url), 'utf8')

  assert.match(source, /佣金（万分之）/)
  assert.match(source, /印花税（千分之）/)
  assert.match(source, /滑点（万分之）/)
})
