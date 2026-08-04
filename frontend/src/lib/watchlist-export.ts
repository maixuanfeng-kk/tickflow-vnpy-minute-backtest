export interface WatchlistExportEntry {
  symbol: string
  name?: string | null
  note?: string | null
}

function escapeCsvValue(value: string | null | undefined): string {
  const text = value ?? ''
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

export function buildWatchlistCsv(entries: WatchlistExportEntry[]): string {
  const rows = entries.map(entry => [entry.symbol, entry.name, entry.note]
    .map(escapeCsvValue)
    .join(','))

  return `\uFEFF代码,名称,备注\r\n${rows.join('\r\n')}${rows.length ? '\r\n' : ''}`
}

export function getWatchlistExportFilename(poolLabel: string): string {
  return `自选股_${poolLabel.replace(/[\\/:*?"<>|]/g, '_')}.csv`
}
