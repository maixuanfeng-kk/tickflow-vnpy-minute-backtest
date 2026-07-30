export function participationRatio(value: string): number {
  const percent = Number(value)
  if (!Number.isFinite(percent)) return 0
  return Math.min(Math.max(percent, 0), 100) / 100
}

/** 开盘量组合回测优先使用手工池，未指定时回退到当前自选列表。 */
export function resolveOpeningVolumePool(selectedSymbols: string, watchlistSymbols: string[]): string[] {
  const source = selectedSymbols.trim() ? selectedSymbols.split(',') : watchlistSymbols
  return Array.from(new Set(source.map(symbol => symbol.trim()).filter(Boolean)))
}
