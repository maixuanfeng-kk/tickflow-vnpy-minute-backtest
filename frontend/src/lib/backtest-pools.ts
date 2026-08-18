export type BacktestPoolSource = 'manual' | 'pool'

export const ETF_159915_STRATEGY_ID = 'etf_159915_minute'
export const ETF_159915_STOCK_POOL_STRATEGY_ID = 'etf_159915_stock_pool'
export const ETF_159915_COMPONENT_WEIGHTED_STRATEGY_ID = 'etf_159915_component_weighted'

export function is159915Strategy(strategyId: string): boolean {
  return strategyId === ETF_159915_STRATEGY_ID
}

export function is159915ComponentWeightedStrategy(strategyId: string): boolean {
  return strategyId === ETF_159915_COMPONENT_WEIGHTED_STRATEGY_ID
}

export function is159915StockPoolStrategy(strategyId: string): boolean {
  return strategyId === ETF_159915_STOCK_POOL_STRATEGY_ID
}

export function usesManagedMonthlyPools(strategyId: string): boolean {
  return is159915StockPoolStrategy(strategyId)
}

export function formatMonthlyPoolCounts(counts: Record<string, number> | null | undefined): string {
  return Object.entries(counts ?? {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([month, count]) => `${month} ${count}只`)
    .join(' · ')
}

export function normalizeBacktestSymbols(values: string[]): string[] {
  const seen = new Set<string>()
  for (const raw of values) {
    const value = raw.trim().toUpperCase()
    const match = value.match(/^(\d{6})(?:\.(XSHG|XSHE|XBSE|SH|SZ|BJ))?$/)
    if (!match) continue
    const [, code, suffix] = match
    const exchange = suffix === 'XSHG' || suffix === 'SH' || (!suffix && code.startsWith('6'))
      ? 'SH'
      : suffix === 'XBSE' || suffix === 'BJ' || (!suffix && /^[48]/.test(code))
        ? 'BJ'
        : 'SZ'
    seen.add(`${code}.${exchange}`)
  }
  return [...seen]
}

export function resolveBacktestSymbols(input: {
  source: BacktestPoolSource
  poolSymbols: string[]
  manualSymbols: string[]
}): string[] {
  return normalizeBacktestSymbols(input.source === 'pool' ? input.poolSymbols : input.manualSymbols)
}

export function canRunBacktest(input: {
  source: BacktestPoolSource
  poolSymbols: string[]
  manualSymbols?: string[]
}): boolean {
  return resolveBacktestSymbols({
    ...input,
    manualSymbols: input.manualSymbols ?? [],
  }).length > 0
}

export function symbolsFromPoolEntries(entries: Array<{ symbol?: string }>): string[] {
  return normalizeBacktestSymbols(entries.map(entry => entry.symbol ?? ''))
}
