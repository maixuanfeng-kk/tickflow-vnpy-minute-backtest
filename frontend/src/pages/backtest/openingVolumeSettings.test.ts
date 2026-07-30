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
  assert.match(source, /openingVolumeStrategy\s*\?\s*OPENING_VOLUME_ADVANCED_TABS\s*:\s*matrixStrategy/)
})

test('vn.py opening-volume requests preserve an explicit stock range', () => {
  installSseTestDoubles()

  startBacktest({
    strategy_id: 'opening_volume_portfolio',
    symbols: ['600000.SH', '000001.SZ'],
    engine: 'vnpy',
  })

  const query = new URL(openedUrl, 'http://localhost').searchParams
  assert.equal(query.get('symbols'), '600000.SH,000001.SZ')
})

test('vn.py opening-volume accepts a multi-symbol stock pool', () => {
  installSseTestDoubles()

  startBacktest({
    strategy_id: 'opening_volume_portfolio',
    symbols: ['600000.SH', '000001.SZ', '300001.SZ'],
    engine: 'vnpy',
  })

  assert.match(openedUrl, /\/api\/backtest\/vnpy\/stream/)
  assert.doesNotMatch(openedUrl, /仅支持单只股票/)
})

test('vn.py opening-volume requests serialize execution controls', () => {
  installSseTestDoubles()
  startBacktest({
    strategy_id: 'opening_volume_portfolio',
    engine: 'vnpy',
    symbols: ['600000.SH'],
    candidate_sort: 'score',
    entry_fill: 'next_minute_open',
    exit_fill: 'next_minute_open',
    force_close_at_end: false,
    max_buy_volume_ratio: 1,
    max_sell_volume_ratio: 0.5,
  })

  const query = new URL(openedUrl, 'http://localhost').searchParams
  assert.equal(query.get('candidate_sort'), 'score')
  assert.equal(query.get('entry_fill'), 'next_minute_open')
  assert.equal(query.get('exit_fill'), 'next_minute_open')
  assert.equal(query.get('force_close_at_end'), 'false')
  assert.equal(query.get('max_buy_volume_ratio'), '1')
  assert.equal(query.get('max_sell_volume_ratio'), '0.5')
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
  assert.match(backtestSource, /下一根实际存在的分钟 K 开盘成交/)
  assert.match(backtestSource, /单只目标金额 = 当前总资产 × 最大总仓位 ÷ 最大持仓数/)
  assert.match(backtestSource, /期末未平仓/)
  assert.match(backtestSource, /自选股子集/)
  assert.match(backtestSource, /emptyLabel="全部自选股"/)
  assert.match(backtestSource, /emptyDescription="默认使用全部 TickFlow 自选股。"/)
  assert.match(backtestSource, /仅在同一分钟候选超过剩余名额时进行评分和区间筛选/)
  assert.match(editorSource, /hideRiskFields/)
})

test('opening-volume runs submit the visible strategy settings to vn.py', () => {
  const source = readFileSync(new URL('./StrategyBacktest.tsx', import.meta.url), 'utf8')

  assert.match(source, /strategyDetail\.data\?\.id === 'opening_volume_portfolio'/)
  assert.match(source, /engine: 'vnpy'/)
  assert.match(source, /max_buy_volume_ratio: participationRatio\(maxBuyVolumeRatio\)/)
  assert.match(source, /max_sell_volume_ratio: participationRatio\(maxSellVolumeRatio\)/)
  assert.match(source, /basic_filter: requestOverrides\.basic_filter/)
  assert.match(source, /scoring: requestOverrides\.scoring/)
  assert.match(source, /max_hold_days: requestOverrides\.max_hold_days/)
  assert.match(source, /cash_reserve_ratio: 1 - Number\(maxExposure\) \/ 100/)
})

test('opening-volume removes separate engine controls and shows fixed fill rules', () => {
  const source = readFileSync(new URL('./StrategyBacktest.tsx', import.meta.url), 'utf8')

  assert.doesNotMatch(source, /\bengineMode\b/)
  assert.doesNotMatch(source, /\bvnpyStrategyId\b/)
  assert.doesNotMatch(source, /\bvolumeLimitEnabled\b/)
  assert.doesNotMatch(source, /\bminuteDataDir\b/)
  assert.match(source, /买入成交量上限（%）/)
  assert.match(source, /卖出成交量上限（%）/)
  assert.match(source, /0 表示不限制/)
  assert.match(source, /下一根实际存在的分钟 K 开盘成交/)
  assert.match(source, /!openingVolumeStrategy && \(/)
  assert.match(source, /maxExposure: '97'/)
  assert.match(source, /setMaxExposure\(OPENING_VOLUME_DEFAULTS\.maxExposure\)/)
})

test('position backtest fee fields name their units explicitly', () => {
  const source = readFileSync(new URL('./StrategyBacktest.tsx', import.meta.url), 'utf8')

  assert.match(source, /佣金（万分之）/)
  assert.match(source, /印花税（千分之）/)
  assert.match(source, /滑点（万分之）/)
})
