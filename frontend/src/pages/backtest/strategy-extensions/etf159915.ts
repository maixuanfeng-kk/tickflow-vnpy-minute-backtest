export const ETF_159915_EXTENSION = {
  id: 'etf_159915_minute',
  symbol: '159915.SZ',
  ruleVersion: '2026-08-07',
  fixedSettings: {
    priceBasis: 'raw',
    execution: 'next_real_minute_open',
    capitalUsage: 0.97,
    maxPositions: 1,
    positionSizing: 'equal',
    stampTax: 0,
    tPlusOne: true,
  },
  entryRules: [
    '4.1.1', '4.1.2', '4.2.1', '4.2.2', '4.2.3-1', '4.2.3-2',
    'tail_1', 'tail_2', 'tail_3',
  ],
  exitRules: [
    '5.1.1', '5.1.2', '5.1.3', '5.2', '5.3', '5.4', '5.4_rebuy', '5.5', '5.6',
  ],
  priorities: ['4.2.3 exclusive', '5.4 > 5.3 > 5.2 > regular exits'],
} as const

export function resolveBacktestExtension(strategyId: string | null | undefined) {
  return strategyId === ETF_159915_EXTENSION.id ? ETF_159915_EXTENSION : null
}

export function isEtf159915RunBlocked(state: {
  isLoading?: boolean
  isError?: boolean
  data?: { ready?: boolean } | null
}) {
  return Boolean(state.isLoading || state.isError || !state.data?.ready)
}

export function buildEtf159915ExecutionRows(
  signals: Array<{
    id: number
    symbol: string
    direction: string
    timestamp: string
    reason: string
    conditions?: string[]
    status: string
    due_at?: string | null
    fill_datetime?: string | null
    rejection_reason?: string | null
  }>,
  fills: Array<{
    signal_id?: number | null
    entry_datetime?: string | null
    entry_price?: number | null
    shares?: number | null
    commission?: number | null
    stamp_tax?: number | null
    slippage?: number | null
  }>,
) {
  const fillsBySignal = new Map(fills.map(fill => [fill.signal_id, fill]))
  return signals.map(signal => {
    const fill = fillsBySignal.get(signal.id)
    return {
      signalId: signal.id,
      symbol: signal.symbol,
      direction: signal.direction,
      signalTime: signal.timestamp,
      dueAt: signal.due_at ?? null,
      status: signal.status,
      reason: signal.reason,
      conditions: signal.conditions ?? [],
      fillDatetime: fill?.entry_datetime ?? signal.fill_datetime ?? null,
      fillPrice: fill?.entry_price ?? null,
      shares: fill?.shares ?? null,
      commission: fill?.commission ?? null,
      stampTax: fill?.stamp_tax ?? null,
      slippage: fill?.slippage ?? null,
      rejectionReason: signal.rejection_reason ?? null,
    }
  })
}
