export interface TradeDateSource {
  entry_date?: string | null
  exit_date?: string | null
  entry_datetime?: string | null
  exit_datetime?: string | null
}

export interface ResolvedTradeDates {
  entry: string
  exit: string
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

function validIsoDate(value: string | null | undefined): string | null {
  const candidate = String(value ?? '').slice(0, 10)
  if (!ISO_DATE.test(candidate)) return null
  const [year, month, day] = candidate.split('-').map(Number)
  const parsed = new Date(Date.UTC(year, month - 1, day))
  if (
    parsed.getUTCFullYear() !== year
    || parsed.getUTCMonth() !== month - 1
    || parsed.getUTCDate() !== day
  ) return null
  return candidate
}

export function addCalendarDays(value: string, days: number): string | null {
  const date = validIsoDate(value)
  if (!date) return null
  const [year, month, day] = date.split('-').map(Number)
  const parsed = new Date(Date.UTC(year, month - 1, day))
  parsed.setUTCDate(parsed.getUTCDate() + days)
  return parsed.toISOString().slice(0, 10)
}

export function resolveTradeDates(trade: TradeDateSource): ResolvedTradeDates | null {
  const entry = validIsoDate(trade.entry_date) ?? validIsoDate(trade.entry_datetime)
  const exit = validIsoDate(trade.exit_date) ?? validIsoDate(trade.exit_datetime)
  return entry && exit ? { entry, exit } : null
}
