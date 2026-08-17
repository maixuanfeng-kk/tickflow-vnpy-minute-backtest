import { Fragment, useState, useMemo, useEffect, useRef, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Play, FlaskConical, Clock, Loader2, Square, Search, Plus, X, SlidersHorizontal, Zap, ListPlus, Download } from 'lucide-react'
import {
  api,
  type StrategyBacktestResult,
  type StrategyBacktestTrade,
  type StrategyDetail,
  type StrategyParamDef,
  type VnpyStrategy,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { storage } from '@/lib/storage'
import { fmtPct, fmtPrice, priceColorClass } from '@/lib/format'
import { BUILTIN_COLUMNS } from '@/lib/watchlist-columns'
import { SignalPicker } from '@/components/screener/SignalPicker'
import { startBacktest, stopBacktest, tryReconnect, useBacktestTask } from '@/lib/backtestTask'
import { useDataStatus } from '@/lib/useSharedQueries'
import { EmptyState } from '@/components/EmptyState'
import { WarmupBadge } from '@/components/WarmupBadge'
import { DatePicker } from '@/components/DatePicker'
import { StrategyNavChart } from './charts/StrategyNavChart'
import { ReturnDistributionChart } from './charts/ReturnDistributionChart'
import { TradeKlineModal } from './components/TradeKlineModal'
import { SignalTriggerActions } from '@/components/signals/SignalTriggerActions'
import {
  Etf159915DataReadiness,
  Etf159915ExecutionTrace,
  Etf159915RuleSummary,
} from './strategy-extensions/Etf159915BacktestExtension'
import { isEtf159915RunBlocked } from './strategy-extensions/etf159915'
import {
  canRunBacktest,
  formatMonthlyPoolCounts,
  is159915Strategy,
  normalizeBacktestSymbols,
  resolveBacktestSymbols,
  symbolsFromPoolEntries,
  usesManagedMonthlyPools,
  type BacktestPoolSource,
} from '@/lib/backtest-pools'

const formatDate = (date: Date) => date.toISOString().slice(0, 10)
const monthsAgo = (months: number) => {
  const date = new Date()
  date.setMonth(date.getMonth() - months)
  return formatDate(date)
}
const TODAY = formatDate(new Date())
const THREE_MONTHS_AGO = monthsAgo(3)

const VNPY_PORTFOLIO_DEFAULT_PARAMS = {
  cash_reserve_ratio: 0.03,
  min_commission: 5.0,
}

type QuickRangeUnit = 'month' | 'year' | 'all'
type QuickRangeConfig = { id: string; enabled: boolean; unit: QuickRangeUnit; value: number }

const QUICK_RANGE_LIMITS = {
  month: { min: 1, max: 120 },
  year: { min: 1, max: 10 },
} as const
const DEFAULT_QUICK_RANGES: QuickRangeConfig[] = [
  { id: 'range-1', enabled: true, unit: 'month', value: 3 },
  { id: 'range-2', enabled: true, unit: 'month', value: 6 },
  { id: 'range-3', enabled: true, unit: 'year', value: 1 },
  { id: 'range-4', enabled: true, unit: 'all', value: 0 },
]
const quickRangeValue = (unit: QuickRangeUnit, value: unknown, fallback: number) => {
  if (unit === 'all') return 0
  const limits = QUICK_RANGE_LIMITS[unit]
  const num = Number(value)
  const safe = Number.isFinite(num) ? Math.round(num) : fallback
  return clamp(safe, limits.min, limits.max)
}
const normalizeQuickRange = (raw: unknown, fallback: QuickRangeConfig): QuickRangeConfig => {
  const obj = raw && typeof raw === 'object' ? raw as Partial<QuickRangeConfig> : {}
  const unit: QuickRangeUnit = obj.unit === 'month' || obj.unit === 'year' || obj.unit === 'all'
    ? obj.unit
    : fallback.unit
  const enabled = typeof obj.enabled === 'boolean' ? obj.enabled : fallback.enabled
  return { id: fallback.id, enabled, unit, value: quickRangeValue(unit, obj.value, fallback.value) }
}
const normalizeQuickRanges = (raw: unknown) => {
  const items = Array.isArray(raw) ? raw : []
  const ranges = DEFAULT_QUICK_RANGES.map((fallback, index) => {
    const byId = items.find(item => item && typeof item === 'object' && (item as { id?: unknown }).id === fallback.id)
    return normalizeQuickRange(byId ?? items[index], fallback)
  })
  return ranges.some(range => range.enabled)
    ? ranges
    : ranges.map((range, index) => index === 0 ? { ...range, enabled: true } : range)
}
const loadQuickRanges = () => normalizeQuickRanges(storage.strategyBacktestQuickRanges.get(DEFAULT_QUICK_RANGES))
const quickRangeMonths = (range: QuickRangeConfig) => range.unit === 'year' ? range.value * 12 : range.value
const quickRangeLabel = (range: QuickRangeConfig) => range.unit === 'all'
  ? '全部'
  : range.unit === 'year'
    ? `${range.value}年`
    : `${range.value}个月`
const quickRangeTitle = (range: QuickRangeConfig) => range.unit === 'all'
  ? '全部历史'
  : range.unit === 'year'
    ? `近 ${range.value} 年`
    : `近 ${range.value} 个月`

const INPUT_CLS = `w-full px-2.5 py-1.5 rounded-input bg-surface border border-border text-xs
  focus:outline-none focus:border-accent transition-colors duration-150 ease-smooth`

const SRC_MAP: Record<string, string> = { builtin: '内置', custom: '自定义', ai: 'AI' }
const TRADE_PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100]

const VNPY_REJECTION_LABELS: Record<string, string> = {
  max_positions: '已达最大持仓数',
  no_next_bar: '信号后无下一根分钟 K 线',
  no_position: '当前无可卖持仓',
  t_plus_one: 'T+1 限制：当日买入不可卖出',
  already_held: '已有持仓或买入委托',
  insufficient_cash: '可用资金不足',
  suspended_or_missing_bar: '停牌或缺少下一分钟数据',
  price_limit: '涨跌停限制，无法成交',
  below_minimum_lot: '资金不足最小买入单位',
  below_lot_or_no_position: '可卖数量不足一手',
}

function vnpySignalExecutionText(item: { status?: string; fill_datetime?: string | null; due_at?: string | null; rejection_reason?: string | null }) {
  if (item.status === 'filled') return item.fill_datetime ? `已成交 · ${item.fill_datetime}` : '已成交'
  if (item.status === 'rejected') return VNPY_REJECTION_LABELS[item.rejection_reason ?? ''] ?? item.rejection_reason ?? '未执行'
  if (item.status === 'queued') return item.due_at ? `等待下一分钟开盘 · ${item.due_at}` : '等待下一分钟开盘'
  return '已触发，等待处理'
}

function isVnpyLongDirection(direction?: string | null) {
  return direction === 'LONG' || direction === 'long' || direction === '多'
}
const BADGE_CLS_MAP: Record<string, string> = {
  builtin: 'bg-secondary/10 text-muted border-border',
  ai: 'bg-purple-500/10 text-purple-400 border-purple-500/30',
  custom: 'bg-amber-400/10 text-amber-400 border-amber-400/30',
}
const FIELD_LABEL: Record<string, string> = {}
for (const c of BUILTIN_COLUMNS) {
  if (c.source.type === 'builtin') FIELD_LABEL[c.source.key] = c.label
}
Object.assign(FIELD_LABEL, {
  change_pct: '涨跌幅', consecutive_limit_ups: '连板',
  momentum_60d: '60D动量', turnover_rate: '换手率',
  rsi_14: 'RSI14', rsi_6: 'RSI6', rsi_24: 'RSI24',
  vol_ratio_5d: '量比', vol_ratio_20d: '20日量比',
  macd_dif: 'MACD-DIF', macd_dea: 'MACD-DEA', macd_hist: 'MACD柱',
  boll_upper: '布林上轨', boll_lower: '布林下轨',
})
const BOARD_OPTIONS = ['沪主板', '深主板', '创业板', '科创板', '北交所']
const BASIC_FILTER_FIELDS = [
  { key: 'price_min', label: '最低价', unit: '元' },
  { key: 'price_max', label: '最高价', unit: '元' },
  { key: 'amount_min', label: '最低成交额', unit: '亿', scale: 1e8 },
  { key: 'market_cap_min', label: '最低总市值', unit: '亿', scale: 1e8 },
  { key: 'turnover_min', label: '最低换手率', unit: '%' },
  { key: 'turnover_max', label: '最高换手率', unit: '%' },
]
type AdvancedSettingsTab = 'params' | 'filter' | 'entry' | 'exit' | 'scoring' | 'risk' | 'range'
type StrategyGroup = 'all' | 'custom' | 'ai' | 'builtin'
const STRATEGY_GROUPS: { id: StrategyGroup; label: string }[] = [
  { id: 'all', label: '全部' },
  { id: 'custom', label: '自定义' },
  { id: 'ai', label: 'AI' },
  { id: 'builtin', label: '内置' },
]
const ADVANCED_TABS: { id: AdvancedSettingsTab; label: string }[] = [
  { id: 'params', label: '策略参数' },
  { id: 'filter', label: '基础过滤' },
  { id: 'entry', label: '买入触发器' },
  { id: 'exit', label: '卖出触发器' },
  { id: 'scoring', label: '评分权重' },
  { id: 'risk', label: '风控' },
  { id: 'range', label: '回测范围' },
]
const toSignalId = (sig: string) => (sig.startsWith('signal_') || sig.startsWith('csg_')) ? sig : `signal_${sig}`
const numOrNull = (v: string) => v === '' || Number.isNaN(Number(v)) ? null : Number(v)
const clamp = (v: number, min?: number, max?: number) => {
  let next = v
  if (min != null) next = Math.max(next, min)
  if (max != null) next = Math.min(next, max)
  return next
}
const strategyDefaultParams = (detail: StrategyDetail) => {
  const values: Record<string, any> = { ...detail.params_defaults }
  detail.params.forEach(p => {
    if (!(p.id in values)) values[p.id] = p.default
  })
  return values
}
const mergeStrategyParams = (detail: StrategyDetail, values?: Record<string, any> | null) => ({
  ...strategyDefaultParams(detail),
  ...(values ?? {}),
})
const buildDefaultOverrides = (detail: StrategyDetail) => ({
  basic_filter: { ...detail.basic_filter },
  entry_signals: detail.entry_signals.map(toSignalId),
  exit_signals: detail.exit_signals.map(toSignalId),
  scoring: { ...detail.scoring },
  stop_loss: detail.stop_loss,
  take_profit: detail.take_profit,
  trailing_stop: detail.trailing_stop,
  trailing_take_profit_activate: detail.trailing_take_profit_activate,
  trailing_take_profit_drawdown: detail.trailing_take_profit_drawdown,
  score_min: null,
  score_max: null,
  max_hold_days: detail.max_hold_days,
})

const fmtMoney = (v: number | null | undefined) => {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const fmtSignedMoney = (v: number | null | undefined) => {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${fmtMoney(v)}`
}

const fmtShares = (v: number | null | undefined) => {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

const fmtLots = (v: number | null | undefined) => {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

const statValueColor = (v: number | null | undefined) => {
  // 中性值继承页面前景色 (亮暗主题都可读), 不再写死近白色
  if (v == null || Number.isNaN(v) || v === 0) return 'inherit'
  return v > 0 ? '#f87171' : '#34d399'
}

function ExitReasonBadge({ reason }: { reason: string }) {
  const config: Record<string, { label: string; cls: string }> = {
    signal: { label: '信号', cls: 'bg-accent/10 text-accent border-accent/30' },
    stop_loss: { label: '止损', cls: 'bg-red-500/10 text-red-400 border-red-500/30' },
    take_profit: { label: '止盈', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
    trailing_stop: { label: '移损', cls: 'bg-orange-500/10 text-orange-400 border-orange-500/30' },
    trailing_take_profit: { label: '回撤止盈', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
    max_hold: { label: '超期', cls: 'bg-amber-400/10 text-amber-400 border-amber-400/30' },
    pending_exit: { label: '待卖', cls: 'bg-orange-400/10 text-orange-400 border-orange-400/30' },
    end: { label: '期末', cls: 'bg-secondary/10 text-secondary border-border' },
  }
  const c = config[reason] ?? { label: reason, cls: 'bg-elevated text-muted border-border' }
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border ${c.cls}`}>{c.label}</span>
  )
}

function TradeLegCell({ trade, side }: { trade: StrategyBacktestTrade; side: 'buy' | 'sell' }) {
  const isBuy = side === 'buy'
  const date = String(isBuy ? trade.entry_date : trade.exit_date).slice(0, 10)
  const signalDate = String(isBuy ? trade.entry_signal_date ?? '' : trade.exit_signal_date ?? '').slice(0, 10)
  const price = isBuy ? trade.entry_price : trade.exit_price
  const amount = isBuy ? trade.entry_value : trade.exit_value

  return (
    <div className="min-w-[8.25rem] rounded-btn border border-border/60 bg-base/35 px-2 py-1 text-xs leading-4">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-secondary">{date}</span>
        <span className={`rounded px-1.5 py-px text-[10px] font-medium ${
          isBuy ? 'bg-accent/15 text-accent' : 'bg-elevated text-secondary'
        }`}>
          {isBuy ? '买' : '卖'}
        </span>
      </div>
      <div className="mt-0.5 flex items-center justify-between gap-2">
        <span className="num text-foreground">{fmtPrice(price)}</span>
        <span className="num font-medium text-foreground">{fmtMoney(amount)}</span>
      </div>
      {signalDate && signalDate !== date && (
        <div className="mt-0.5 text-[10px] text-muted">信号 {signalDate}</div>
      )}
    </div>
  )
}

function fmtDuration(ms: number): string {
  const s = ms / 1000
  if (s < 1) return `${ms.toFixed(0)}ms`
  if (s < 60) return `${s.toFixed(1)}秒`
  const m = Math.floor(s / 60)
  const rest = Math.round(s % 60)
  return `${m}分${rest}秒`
}

function SharpeLabel() {
  const [open, setOpen] = useState(false)
  const [alignRight, setAlignRight] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [open])
  const toggle = () => {
    if (!open && ref.current) {
      const rect = ref.current.getBoundingClientRect()
      setAlignRight(rect.left + 240 > window.innerWidth)
    }
    setOpen(o => !o)
  }
  return (
    <span className="relative inline-flex items-center gap-1" ref={ref}>
      夏普
      <button
        type="button"
        onClick={toggle}
        className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-full border border-border bg-base text-[10px] text-muted transition-colors hover:border-accent/50 hover:text-accent"
      >
        ?
      </button>
      {open && (
        <span className={`absolute top-full z-50 mt-1.5 w-60 max-w-[calc(100vw-1.5rem)] rounded-lg border border-border bg-elevated px-3 py-2.5 text-[11px] leading-relaxed text-secondary shadow-xl ${alignRight ? 'right-0' : 'left-0'}`}>
          <span className="block font-medium text-foreground">夏普比率 (Sharpe Ratio)</span>
          <span className="mt-1 block">衡量<b className="text-foreground">单位波动风险</b>换来的超额收益。</span>
          <span className="mt-0.5 block">数值越高，收益相对波动越优秀；</span>
          <span className="mt-0.5 block text-warning">短周期或交易次数少时容易偏高，仅供参考。</span>
        </span>
      )}
    </span>
  )
}

function Stat({ label, value, color }: { label: ReactNode; value: string; color?: string }) {
  return (
    <div className="min-w-0 rounded-btn border border-border/70 bg-elevated/70 px-3 py-2">
      <div className="text-[11px] text-secondary">{label}</div>
      <div
        className="mt-1 break-words text-sm font-mono font-semibold leading-tight tracking-tight num xl:text-base"
        style={{ color: color ?? 'inherit' }}
        title={value}
      >
        {value}
      </div>
    </div>
  )
}

function ConfigSection({ title, hint, actions, children }: { title: string; hint?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-btn border border-border bg-surface/70 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="text-xs font-medium text-foreground">
          {title}
          {hint && <span className="ml-1 text-[10px] font-normal text-muted">{hint}</span>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
      </div>
      <div className="mt-3 space-y-2">{children}</div>
    </div>
  )
}


const scoringToPct = (values: Record<string, number>) => {
  const total = Object.values(values).reduce((a, b) => a + Math.max(0, Number(b) || 0), 0)
  if (total <= 0) return Object.fromEntries(Object.keys(values).map(k => [k, 0])) as Record<string, number>
  return Object.fromEntries(Object.entries(values).map(([k, v]) => [k, Math.round((Math.max(0, Number(v) || 0) / total) * 100)])) as Record<string, number>
}

const normalizePctWeights = (values: Record<string, number>) => {
  const total = Object.values(values).reduce((a, b) => a + Math.max(0, Number(b) || 0), 0)
  if (total <= 0) return Object.fromEntries(Object.keys(values).map(k => [k, 0])) as Record<string, number>
  return Object.fromEntries(Object.entries(values).map(([k, v]) => [k, +(Math.max(0, Number(v) || 0) / total).toFixed(4)])) as Record<string, number>
}

function ScoringWeightRow({ name, weight, pct, editing, onChange }: {
  name: string
  weight: number
  pct: number
  editing: boolean
  onChange: (value: number) => void
}) {
  const label = FIELD_LABEL[name] ?? name
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 shrink-0 truncate text-right text-[11px] text-secondary" title={name}>{label}</span>
      {editing ? (
        <input
          type="range"
          min={0}
          max={100}
          step={1}
          value={weight}
          onChange={e => onChange(Number(e.target.value))}
          className="h-1 flex-1 cursor-pointer accent-amber-400"
        />
      ) : (
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-elevated">
          <div className="h-full rounded-full bg-amber-400/70 transition-all duration-300" style={{ width: `${Math.min(pct, 100)}%` }} />
        </div>
      )}
      <span className="w-10 text-right font-mono text-[10px] text-muted">{editing ? weight : `${pct}%`}</span>
    </div>
  )
}

function StrategyParamInput({ param, value, onChange }: {
  param: StrategyParamDef
  value: any
  onChange: (value: any) => void
}) {
  if (param.type === 'bool') {
    const checked = value === true || value === 'true' || value === 'True' || value === true
    return (
      <label className="block">
        <span className="mb-1 block text-[11px] text-secondary">{param.label}</span>
        <button
          type="button"
          onClick={() => onChange(!checked)}
          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors duration-200 cursor-pointer ${
            checked ? 'bg-accent shadow-[0_0_6px_rgba(59,130,246,0.3)]' : 'bg-elevated'
          }`}
          aria-pressed={checked}
        >
          <span className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
            checked ? 'translate-x-[18px]' : 'translate-x-0.5'
          }`} />
        </button>
      </label>
    )
  }
  if (param.type === 'select') {
    return (
      <label className="block">
        <span className="mb-1 block text-[11px] text-secondary">{param.label}</span>
        <select value={value ?? param.default} onChange={e => onChange(e.target.value)} className={INPUT_CLS}>
          {(param.options ?? []).map(opt => <option key={opt} value={opt}>{opt}</option>)}
        </select>
      </label>
    )
  }
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-secondary">{param.label}</span>
      <input
        type="number"
        value={value ?? ''}
        min={param.min}
        max={param.max}
        step={param.step ?? (param.type === 'int' ? 1 : 0.01)}
        onChange={e => {
          const n = numOrNull(e.target.value)
          if (n == null) return onChange('')
          const next = clamp(n, param.min, param.max)
          onChange(param.type === 'int' ? Math.round(next) : next)
        }}
        className={INPUT_CLS}
      />
    </label>
  )
}

function VnpyStrategyParamInput({ param, value, onChange }: {
  param: VnpyStrategy['parameters'][number]
  value: unknown
  onChange: (value: unknown) => void
}) {
  if (param.kind === 'bool') {
    return (
      <label className="flex items-center justify-between gap-2 text-[11px] text-secondary">
        {param.label}
        <input type="checkbox" checked={value === true} onChange={event => onChange(event.target.checked)} className="h-3.5 w-3.5 accent-amber-400" />
      </label>
    )
  }
  if (param.kind === 'string' || param.kind === 'time') {
    return (
      <label className="block">
        <span className="mb-1 block text-[11px] text-secondary">{param.label}</span>
        <input type="text" value={String(value ?? param.default ?? '')} onChange={event => onChange(event.target.value)} className={INPUT_CLS} />
      </label>
    )
  }
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-secondary">{param.label}</span>
      <input
        type="number"
        value={typeof value === 'number' ? value : Number(param.default ?? 0)}
        min={param.minimum ?? undefined}
        max={param.maximum ?? undefined}
        step={param.kind === 'int' ? 1 : 0.01}
        onChange={event => onChange(param.kind === 'int' ? Math.round(Number(event.target.value)) : Number(event.target.value))}
        className={INPUT_CLS}
      />
    </label>
  )
}

function parseBulkPoolSymbols(raw: string): { symbols: string[]; invalid: number } {
  const seen = new Set<string>()
  let invalid = 0
  for (const token of raw.split(/[\s,;，；\[\]\(\){}'"`]+/)) {
    if (!token) continue
    const normalized = token.trim().toUpperCase()
    const match = normalized.match(/^(\d{6})(?:\.(XSHG|XSHE|XBSE|SH|SZ|BJ|SSE|SZSE|BSE))?$/)
    if (!match) {
      invalid += 1
      continue
    }
    const [, code, suffix] = match
    let exchange: string
    if (["XSHG", "SH", "SSE"].includes(suffix ?? "")) exchange = "SH"
    else if (["XSHE", "SZ", "SZSE"].includes(suffix ?? "")) exchange = "SZ"
    else if (["XBSE", "BJ", "BSE"].includes(suffix ?? "")) exchange = "BJ"
    else if (code.startsWith("6")) exchange = "SH"
    else if (code.startsWith("4") || code.startsWith("8")) exchange = "BJ"
    else exchange = "SZ"
    seen.add(`${code}.${exchange}`)
  }
  return { symbols: [...seen], invalid }
}

function StockPoolPicker({
  value,
  onChange,
  onPoolStateChange,
}: {
  value: string
  onChange: (value: string) => void
  onPoolStateChange?: (state: { source: BacktestPoolSource; poolKey: string | null; symbols: string[] }) => void
}) {
  const symbols = useMemo(() => value.split(',').map(s => s.trim()).filter(Boolean), [value])
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [bulkText, setBulkText] = useState('')
  const [bulkMessage, setBulkMessage] = useState<string | null>(null)
  const [showAllSymbols, setShowAllSymbols] = useState(false)
  const [symbolNames, setSymbolNames] = useState<Record<string, string>>({})
  const [poolSource, setPoolSource] = useState<BacktestPoolSource>('manual')
  const [selectedPoolKey, setSelectedPoolKey] = useState<string | null>(null)
  const ref = useRef<HTMLDivElement>(null)
  const pools = useQuery({
    queryKey: QK.watchlistPools,
    queryFn: api.watchlistPools,
    staleTime: 30_000,
  })
  const search = useQuery({
    queryKey: QK.instrumentSearch(query),
    queryFn: () => api.instrumentSearch(query),
    enabled: query.trim().length > 0,
    staleTime: 30_000,
  })
  const results = search.data?.results ?? []
  // 自选列表 — 供「从自选导入」一键填入回测范围
  const watchlist = useQuery({
    queryKey: QK.watchlist(),
    queryFn: () => api.watchlistList(),
    staleTime: 30_000,
  })

  useEffect(() => {
    if (results.length === 0) return
    setSymbolNames(prev => {
      const next = { ...prev }
      results.forEach(r => {
        if (r.name) next[r.symbol] = r.name
      })
      return next
    })
  }, [results])

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const setManualSymbols = (next: string[]) => {
    setPoolSource('manual')
    setSelectedPoolKey(null)
    const normalized = normalizeBacktestSymbols(next)
    onChange(normalized.join(','))
    onPoolStateChange?.({ source: 'manual', poolKey: null, symbols: normalized })
  }
  const addSymbol = (symbol: string, name?: string | null) => {
    if (name) setSymbolNames(prev => ({ ...prev, [symbol]: name }))
    setManualSymbols([...symbols, symbol])
    setQuery('')
    setOpen(false)
  }
  const removeSymbol = (symbol: string) => setManualSymbols(symbols.filter(s => s !== symbol))
  const applyBulkPool = () => {
    const parsed = parseBulkPoolSymbols(bulkText)
    if (parsed.symbols.length === 0) {
      setBulkMessage('未识别到有效股票代码。')
      return
    }
    setManualSymbols(parsed.symbols)
    setBulkMessage(`已导入 ${parsed.symbols.length} 只${parsed.invalid ? `；跳过 ${parsed.invalid} 项` : ''}`)
    setShowAllSymbols(false)
  }
  // 一键导入自选: 合并去重, 顺带回填股票名
  const importFromWatchlist = () => {
    const entries = watchlist.data?.symbols ?? []
    if (entries.length === 0) return
    setSymbolNames(prev => {
      const next = { ...prev }
      entries.forEach(e => { if (e.name) next[e.symbol] = e.name })
      return next
    })
    setManualSymbols([...symbols, ...entries.map(e => e.symbol)])
  }
  const watchlistCount = watchlist.data?.symbols?.length ?? 0
  const selectedPoolQuery = useQuery({
    queryKey: QK.watchlist(selectedPoolKey ?? undefined, selectedPoolKey === 'all' ? 'all' : undefined),
    queryFn: () => selectedPoolKey === 'all' ? api.watchlistList(undefined, 'all') : api.watchlistList(selectedPoolKey ?? undefined),
    enabled: poolSource === 'pool' && selectedPoolKey !== null,
    staleTime: 30_000,
  })
  const selectedPoolSymbols = useMemo(
    () => symbolsFromPoolEntries(selectedPoolQuery.data?.symbols ?? []),
    [selectedPoolQuery.data?.symbols],
  )

  useEffect(() => {
    if (poolSource !== 'pool' || selectedPoolKey === null || !selectedPoolQuery.data) return
    onChange(selectedPoolSymbols.join(','))
    onPoolStateChange?.({ source: 'pool', poolKey: selectedPoolKey, symbols: selectedPoolSymbols })
  }, [onChange, onPoolStateChange, poolSource, selectedPoolKey, selectedPoolQuery.data, selectedPoolSymbols])

  const selectPool = (poolKey: string | null) => {
    setPoolSource(poolKey === null ? 'manual' : 'pool')
    setSelectedPoolKey(poolKey)
    if (poolKey === null) {
      onPoolStateChange?.({ source: 'manual', poolKey: null, symbols: normalizeBacktestSymbols(symbols) })
    } else {
      onPoolStateChange?.({ source: 'pool', poolKey, symbols: [] })
    }
  }

  return (
    <div className="space-y-2" ref={ref}>
      <div className="flex flex-wrap items-center gap-1.5 rounded-input border border-border/70 bg-surface/50 p-1.5">
        <span className="px-1 text-[10px] font-medium uppercase tracking-wide text-muted">股票池</span>
        <button type="button" onClick={() => selectPool(null)} className={`rounded-input px-2 py-1 text-[11px] transition-colors ${poolSource === 'manual' ? 'bg-accent/15 text-accent' : 'text-secondary hover:bg-elevated'}`}>手动编辑</button>
        <button type="button" onClick={() => selectPool('all')} className={`rounded-input px-2 py-1 text-[11px] transition-colors ${selectedPoolKey === 'all' ? 'bg-accent/15 text-accent' : 'text-secondary hover:bg-elevated'}`}>全部自选</button>
        {pools.data?.pools.map(pool => (
          <button key={pool.pool_key} type="button" onClick={() => selectPool(pool.pool_key)} className={`rounded-input px-2 py-1 text-[11px] transition-colors ${selectedPoolKey === pool.pool_key ? 'bg-accent/15 text-accent' : 'text-secondary hover:bg-elevated'}`}>
            {pool.label ?? pool.month ?? '未分组'} · {pool.member_count}
          </button>
        ))}
        {pools.isError && <span className="px-1 text-[10px] text-danger">自选池加载失败，可手动编辑</span>}
      </div>
      {poolSource === 'pool' && selectedPoolKey !== null && (
        <div className="flex items-center justify-between rounded-input border border-accent/20 bg-accent/5 px-2 py-1.5 text-[11px]">
          <span className="text-secondary">{selectedPoolQuery.isLoading ? '正在加载股票池…' : selectedPoolSymbols.length ? `已选择 ${selectedPoolSymbols.length} 只股票` : '所选自选池为空'}</span>
          <span className="font-mono text-accent">{selectedPoolKey === 'all' ? 'all' : selectedPoolKey}</span>
        </div>
      )}
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
          <input
            type="text"
            value={query}
            onChange={e => { setQuery(e.target.value); setOpen(true) }}
            onFocus={() => { if (query.trim()) setOpen(true) }}
            placeholder="搜索股票名称/代码添加股票池"
            className="w-full rounded-input border border-border bg-surface py-1.5 pl-8 pr-2.5 text-xs focus:border-accent focus:outline-none"
          />
          {open && results.length > 0 && (
            <div className="absolute left-0 right-0 top-full z-50 mt-1 max-h-56 overflow-y-auto rounded-card border border-border bg-base shadow-xl">
              {results.map(r => {
                const added = symbols.includes(r.symbol)
                return (
                  <button
                    key={r.symbol}
                    type="button"
                    disabled={added}
                    onClick={() => addSymbol(r.symbol, r.name)}
                    className={`flex w-full items-center gap-2 px-3 py-2 text-left text-xs transition-colors ${added ? 'cursor-default text-muted' : 'text-foreground hover:bg-elevated'}`}
                  >
                    <span className="w-[78px] shrink-0 font-mono">{r.symbol}</span>
                    <span className="min-w-0 flex-1 truncate text-secondary">{r.name}</span>
                    <Plus className={`h-3.5 w-3.5 ${added ? 'opacity-30' : 'text-accent'}`} />
                  </button>
                )
              })}
            </div>
          )}
        </div>
        {/* 操作按钮 — 紧贴输入框右侧 */}
        <div className="flex shrink-0 items-center gap-1.5">
          {/* 当前范围 — 有范围显示个数, 无范围显示全市场 */}
          <span className={`whitespace-nowrap text-[11px] font-medium ${symbols.length === 0 ? 'text-amber-400' : 'text-accent'}`}>
            {symbols.length === 0 ? '全市场' : `共 ${symbols.length} 只`}
          </span>
          <button
            type="button"
            onClick={importFromWatchlist}
            disabled={watchlist.isLoading || watchlistCount === 0}
            className="inline-flex items-center gap-1 whitespace-nowrap rounded-input border border-border bg-surface px-2 py-1.5 text-[11px] text-secondary transition-colors hover:border-accent/50 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            title="把自选列表的个股加入回测范围"
          >
            <ListPlus className="h-3 w-3" />
            {watchlist.isLoading ? '加载…' : watchlistCount === 0 ? '自选空' : `导入自选(${watchlistCount})`}
          </button>
          <button
            type="button"
            onClick={() => setBulkOpen(value => !value)}
            className={`inline-flex items-center whitespace-nowrap rounded-input border px-2 py-1.5 text-[11px] transition-colors ${bulkOpen ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border bg-surface text-secondary hover:border-accent/50 hover:text-foreground'}`}
            title="粘贴聚宽、TickFlow 或纯六码股票代码；将自动转换交易所后缀并去重"
          >
            批量粘贴
          </button>
          <button
            type="button"
            onClick={() => setManualSymbols([])}
            disabled={symbols.length === 0}
            className="inline-flex items-center gap-1 whitespace-nowrap rounded-input border border-border bg-surface px-2 py-1.5 text-[11px] text-secondary transition-colors hover:border-danger/50 hover:text-danger disabled:cursor-not-allowed disabled:opacity-50"
            title="清空回测范围"
          >
            <X className="h-3 w-3" />
            清空
          </button>
        </div>
      </div>
      {bulkOpen && (
        <div className="rounded-input border border-accent/25 bg-accent/5 p-2">
          <textarea
            value={bulkText}
            onChange={event => { setBulkText(event.target.value); setBulkMessage(null) }}
            placeholder="粘贴列表，例如 ['688322.XSHG', '002978.XSHE']，也支持逗号、换行或纯六码代码"
            rows={5}
            className="w-full resize-y rounded-input border border-border bg-base px-2 py-1.5 font-mono text-[11px] text-foreground outline-none focus:border-accent"
          />
          <div className="mt-1.5 flex items-center gap-2">
            <button type="button" onClick={applyBulkPool} className="rounded-btn bg-accent px-2 py-1 text-[11px] font-medium text-white hover:bg-accent/90">替换为该股票池</button>
            {bulkMessage && <span className="text-[11px] text-secondary">{bulkMessage}</span>}
          </div>
          <p className="mt-1 text-[10px] text-muted">自动转换：XSHG→SH、XSHE→SZ、XBSE→BJ；重复代码自动去除。</p>
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        {symbols.length === 0 ? (
          <span className="text-[11px] text-muted">默认全市场回测，由基础过滤和策略条件筛选。</span>
        ) : (showAllSymbols ? symbols : symbols.slice(0, 48)).map(symbol => {
          const name = symbolNames[symbol]
          return (
          <span key={symbol} className="inline-flex items-center gap-1 rounded-btn border border-accent/30 bg-accent/10 px-2 py-1 text-[10px] text-accent">
            <span className="font-mono">{symbol}</span>
            {name && <span className="max-w-[7rem] truncate text-accent/80">{name}</span>}
            <button type="button" onClick={() => removeSymbol(symbol)} className="text-accent/70 hover:text-accent">
              <X className="h-3 w-3" />
            </button>
          </span>
          )
        })}
      </div>
      {symbols.length > 48 && (
        <button type="button" onClick={() => setShowAllSymbols(value => !value)} className="text-[11px] text-accent hover:text-accent/80">
          {showAllSymbols ? '收起股票列表' : `显示其余 ${symbols.length - 48} 只`}
        </button>
      )}
    </div>
  )
}

export function StrategyBacktest() {
  const [saved] = useState(() => storage.strategyBacktestLast.get(null))
  const [selectedStrategy, setSelectedStrategy] = useState<string | null>(saved?.selectedStrategy ?? null)
  const [strategyGroup, setStrategyGroup] = useState<StrategyGroup>('all')
  const [symbols, setSymbols] = useState(saved?.symbols ?? '')
  const [poolSource, setPoolSource] = useState<BacktestPoolSource>('manual')
  const [poolSymbols, setPoolSymbols] = useState<string[]>([])
  const [start, setStart] = useState(saved?.start ?? THREE_MONTHS_AGO)
  const [end, setEnd] = useState(saved?.end ?? TODAY)
  const [startTime, setStartTime] = useState(saved?.startTime ?? '09:30')
  const [endTime, setEndTime] = useState(saved?.endTime ?? '15:00')
  // 成交口径: 建仓/清仓可独立配置。向后兼容老 matching (派生为 entry=exit=matching)。
  const [matching] = useState<'close_t' | 'open_t+1'>(saved?.matching ?? 'open_t+1')
  const [entryFill, setEntryFill] = useState<'close_t' | 'open_t+1'>(saved?.entryFill ?? saved?.matching ?? 'open_t+1')
  const [exitFill, setExitFill] = useState<'close_t' | 'open_t+1'>(
    saved?.exitFill === 'open_t+1' ? 'open_t+1' : 'close_t',
  )
  const [fees, setFees] = useState(saved?.fees ?? '2')
  const [stampTax, setStampTax] = useState(saved?.stampTax ?? '1')
  const [slippage, setSlippage] = useState(saved?.slippage ?? '5')
  const [maxPositions, setMaxPositions] = useState(saved?.maxPositions ?? '10')
  const [maxExposure, setMaxExposure] = useState(saved?.maxExposure ?? '100')
  const [initialCapital, setInitialCapital] = useState(saved?.initialCapital ?? '1000000')
  const [positionSizing, setPositionSizing] = useState<'equal' | 'score_weight'>(saved?.positionSizing ?? 'equal')
  const [volumeLimitEnabled, setVolumeLimitEnabled] = useState(saved?.volumeLimitEnabled ?? true)
  const [signalPriceBasis, setSignalPriceBasis] = useState<'qfq' | 'raw'>(saved?.signalPriceBasis ?? 'qfq')
  const simMode = 'position' as const
  const holdingDays = '5'
  const [settingsOpen, setSettingsOpen] = useState(false)
  // vn.py 本地分钟回测；具体股票池规模由注册策略决定。
  // This page is dedicated to the vn.py minute engine. The legacy matrix
  // branch remains in the module only for compatibility with old saved state,
  // but is no longer reachable from the UI.
  const highGranularity = true
  const [vnpyStrategyId, setVnpyStrategyId] = useState('opening_breakout_pool')
  const etf159915Strategy = is159915Strategy(vnpyStrategyId)
  const etf159915StockPoolStrategy = usesManagedMonthlyPools(vnpyStrategyId)
  const [vnpyParams, setVnpyParams] = useState<Record<string, unknown>>(VNPY_PORTFOLIO_DEFAULT_PARAMS)
  const [rangeSettingsOpen, setRangeSettingsOpen] = useState(false)
  const [quickRanges, setQuickRanges] = useState(loadQuickRanges)
  const [settingsTab, setSettingsTab] = useState<AdvancedSettingsTab>('params')
  const [editingScoring, setEditingScoring] = useState(false)
  const [scoringDraft, setScoringDraft] = useState<Record<string, number>>({})
  const [strategyParams, setStrategyParams] = useState<Record<string, any>>(saved?.params ?? {})
  const [overrides, setOverrides] = useState<Record<string, any>>(saved?.overrides ?? {})
  // result 不从 localStorage 恢复:它是运行产物(净值/交易),大且易过时,
  // 跨会话/拉新代码后自动渲染一个可能对应已失效策略的旧结果会造成困惑
  // (切页不卸载组件,内存中的 result 仍保留,无需靠 localStorage 恢复)。
  const [result, setResult] = useState<StrategyBacktestResult | null>(null)
  const [resultTab, setResultTab] = useState<'daily' | 'trades' | 'signals' | 'positions' | 'execution'>('daily')
  const [dailyPage, setDailyPage] = useState(0)
  const [tradePage, setTradePage] = useState(0)
  const [tradePageSize, setTradePageSize] = useState(10)
  const [dailyExpanded, setDailyExpanded] = useState<string | null>(null)
  const [tradeSearch, setTradeSearch] = useState('')
  const [tradeReason, setTradeReason] = useState('')
  const [selectedTrade, setSelectedTrade] = useState<StrategyBacktestTrade | null>(null)
  const loadedStrategyRef = useRef<string | null>(null)

  const strategies = useQuery({
    queryKey: QK.screenerStrategies(),
    queryFn: () => api.screenerStrategies(),
    enabled: !highGranularity,
  })

  const strategyList = useMemo(() => strategies.data?.presets ?? [], [strategies.data])
  const filteredStrategyList = useMemo(() => (
    strategyGroup === 'all' ? strategyList : strategyList.filter(st => st.source === strategyGroup)
  ), [strategyGroup, strategyList])

  // 校验 localStorage 里保存的上次选中策略是否仍存在(本地开发残留的自定义策略
  // 拉新代码后会失效,导致 strategyGet 一直 404/加载中)。列表就绪后若失效,
  // 连带清除其专属的 params/overrides/result(这些是该策略的运行配置/产物,
  // 策略失效后留着会造成"孤儿"状态:界面显示旧回测结果却无对应策略)。
  useEffect(() => {
    if (strategies.isLoading || strategyList.length === 0) return
    if (selectedStrategy && !strategyList.some(st => st.id === selectedStrategy)) {
      setSelectedStrategy(null)
      setStrategyParams({})
      setOverrides({})
      setResult(null)
    }
  }, [strategies.isLoading, strategyList, selectedStrategy])

  const strategyDetail = useQuery({
    queryKey: ['strategy-detail', selectedStrategy],
    queryFn: () => api.strategyGet(selectedStrategy!),
    enabled: !highGranularity && !!selectedStrategy,
  })

  const backtestTask = useBacktestTask()
  const isPending = backtestTask?.isPending ?? false

  const dataStatus = useDataStatus()
  const vnpyStrategies = useQuery({ queryKey: ['vnpy-strategies'], queryFn: api.vnpyStrategies })
  const vnpyStrategy = useMemo<VnpyStrategy | undefined>(
    () => vnpyStrategies.data?.strategies.find(item => item.id === vnpyStrategyId),
    [vnpyStrategies.data, vnpyStrategyId],
  )
  const etfReadiness = useQuery({
    queryKey: ['vnpy-readiness', vnpyStrategyId, '159915.SZ', start, end, startTime, endTime],
    queryFn: () => api.vnpyReadiness(vnpyStrategyId, ['159915.SZ'], start, end, startTime, endTime),
    enabled: (etf159915Strategy || etf159915StockPoolStrategy) && Boolean(
      start && end && startTime && endTime && start <= end && (start !== end || startTime <= endTime),
    ),
    retry: false,
  })
  const earliestDate = dataStatus.data?.daily?.earliest_date ?? null
  const hasLocalMinuteData = !!dataStatus.data?.minute?.trading_days

  useEffect(() => {
    const first = vnpyStrategies.data?.strategies[0]
    if (first && !vnpyStrategies.data?.strategies.some(item => item.id === vnpyStrategyId)) {
      setVnpyStrategyId(first.id)
    }
  }, [vnpyStrategies.data, vnpyStrategyId])

  useEffect(() => {
    if (!vnpyStrategy) return
    setVnpyParams({
      ...VNPY_PORTFOLIO_DEFAULT_PARAMS,
      ...Object.fromEntries(vnpyStrategy.parameters.map(param => [param.name, param.default])),
    })
  }, [vnpyStrategyId, vnpyStrategy])

  useEffect(() => {
    if (!etf159915Strategy) return
    setSymbols('159915.SZ')
    setPoolSource('manual')
    setPoolSymbols([])
    setSignalPriceBasis('raw')
    setMaxPositions('1')
    setPositionSizing('equal')
    setVolumeLimitEnabled(false)
    setStampTax('0')
    setStartTime('09:30')
    setEndTime('15:00')
  }, [etf159915Strategy])

  useEffect(() => {
    if (!etf159915StockPoolStrategy) return
    setStart('2026-05-06')
    setEnd('2026-07-31')
    setInitialCapital('10000000')
    setSignalPriceBasis('raw')
    setMaxPositions('10')
    setPositionSizing('equal')
    setVolumeLimitEnabled(false)
    setStartTime('09:30')
    setEndTime('15:00')
  }, [etf159915StockPoolStrategy])

  const resetConfigFromDetail = (detail: StrategyDetail) => {
    setStrategyParams(strategyDefaultParams(detail))
    setOverrides(buildDefaultOverrides(detail))
  }

  // 刷新页面后: 从 localStorage 恢复未完成的回测任务
  useEffect(() => {
    tryReconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const detail = strategyDetail.data
    if (!detail || loadedStrategyRef.current === detail.id) return
    loadedStrategyRef.current = detail.id
    if (saved?.selectedStrategy === detail.id && (saved.params || saved.overrides)) {
      setStrategyParams(mergeStrategyParams(detail, saved.params))
      setOverrides(saved.overrides ?? buildDefaultOverrides(detail))
      return
    }
    resetConfigFromDetail(detail)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyDetail.data])

  // 当全局回测任务完成时, 把结果写入组件 (切页回来也能恢复)
  useEffect(() => {
    if (backtestTask && !backtestTask.isPending && backtestTask.result) {
      setResult(backtestTask.result)
      setResultTab('daily')
      setDailyPage(0)
      setTradePage(0)
      storage.strategyBacktestLast.set({
        selectedStrategy,
        symbols,
        start,
        end,
        startTime,
        endTime,
        matching,
        entryFill,
        exitFill,
        fees,
        stampTax,
        slippage,
        maxPositions,
        maxExposure,
        initialCapital,
        positionSizing,
        volumeLimitEnabled,
        signalPriceBasis,
        mode: simMode,
        holdingDays,
        params: strategyParams,
        overrides,
        result: backtestTask.result,
      })
    }
  }, [backtestTask])

  const handleRun = () => {
    const requestSymbols = etf159915Strategy ? ['159915.SZ'] : etf159915StockPoolStrategy ? [] : resolveBacktestSymbols({
      source: poolSource,
      poolSymbols,
      manualSymbols: symbols.split(','),
    })
    if (
      (!etf159915StockPoolStrategy && requestSymbols.length === 0)
      || !startTime
      || !endTime
      || (start === end && startTime > endTime)
      || (etf159915Strategy && isEtf159915RunBlocked(etfReadiness))
    ) return
    startBacktest({
      strategy_id: vnpyStrategyId,
      symbols: requestSymbols,
      start: start || null,
      end: end || undefined,
      start_time: startTime,
      end_time: endTime,
      matching,
      entry_fill: entryFill,
      exit_fill: exitFill,
      commission_pct: Number(fees) / 10000,
      stamp_tax_pct: etf159915Strategy ? 0 : Number(stampTax) / 1000,
      slippage_bps: Number(slippage),
      max_positions: etf159915Strategy ? 1 : etf159915StockPoolStrategy ? 10 : Number(maxPositions),
      max_exposure_pct: Number(maxExposure) / 100,
      initial_capital: Number(initialCapital),
      position_sizing: etf159915Strategy || etf159915StockPoolStrategy ? 'equal' : positionSizing,
      volume_limit_enabled: etf159915Strategy || etf159915StockPoolStrategy ? false : volumeLimitEnabled,
      signal_price_basis: etf159915Strategy || etf159915StockPoolStrategy ? 'raw' : signalPriceBasis,
      params: vnpyParams,
      overrides: {},
      mode: simMode,
      holding_days: Number(holdingDays) || 5,
      engine: 'vnpy',
    })
  }
  const requestSymbols = resolveBacktestSymbols({
    source: poolSource,
    poolSymbols,
    manualSymbols: symbols.split(','),
  })
  const hasRunnableSymbols = canRunBacktest({
    source: poolSource,
    poolSymbols,
    manualSymbols: symbols.split(','),
  })
  const etfReadinessBlocked = (etf159915Strategy || etf159915StockPoolStrategy) && isEtf159915RunBlocked(etfReadiness)
  const invalidMinuteRange = !startTime || !endTime || (start === end && startTime > endTime)
  const canRunVnpy = (hasRunnableSymbols || etf159915StockPoolStrategy) && !etfReadinessBlocked && !invalidMinuteRange
  const isEtfResult = result?.strategy_info?.id === 'etf_159915_minute'
  const isEtfStockPoolResult = usesManagedMonthlyPools(result?.strategy_info?.id ?? '')
  const monthlyPoolSummary = formatMonthlyPoolCounts(result?.strategy_info?.monthly_pool_counts)

  // 提取统计
  const s = result?.stats
  const pick = (...keys: string[]) => {
    for (const k of keys) {
      if (s && k in s && s[k] != null) return s[k]
    }
    return null
  }

  const benchmarkReturn = useMemo(() => {
    const values = (result?.benchmark_curve ?? [])
      .map(r => Number(r.close ?? r.value))
      .filter(v => Number.isFinite(v) && v > 0)
    if (values.length < 2) return null
    return values[values.length - 1] / values[0] - 1
  }, [result?.benchmark_curve])

  const strategyReturn = pick('total_return') as number | null
  const excessReturn = strategyReturn != null && benchmarkReturn != null
    ? strategyReturn - benchmarkReturn
    : null
  const isVnpyPortfolio = s?.mode === 'vnpy_portfolio'

  const applyRange = (months: number) => {
    setStart(monthsAgo(months))
    setEnd(formatDate(new Date()))
  }

  const applyAllRange = () => {
    setStart(earliestDate ?? '')
    setEnd(formatDate(new Date()))
  }

  // 进入页面/还在加载时就点了"全部": earliestDate 就绪后回填, 让 DatePicker 显示真实起始日
  useEffect(() => {
    if (earliestDate && start === '' && end === TODAY) {
      setStart(earliestDate)
    }
  }, [earliestDate, start, end])

  const applyQuickRange = (range: QuickRangeConfig) => {
    if (range.unit === 'all') {
      applyAllRange()
      return
    }
    applyRange(quickRangeMonths(range))
  }

  const saveQuickRanges = (next: QuickRangeConfig[]) => {
    const normalized = normalizeQuickRanges(next)
    storage.strategyBacktestQuickRanges.set(normalized)
    return normalized
  }

  const updateQuickRange = (id: string, patch: Partial<Pick<QuickRangeConfig, 'enabled' | 'unit' | 'value'>>) => {
    setQuickRanges(prev => {
      const current = prev.find(range => range.id === id)
      if (patch.enabled === false && current?.enabled && prev.filter(range => range.enabled).length <= 1) return prev
      return saveQuickRanges(prev.map(range => range.id === id
        ? normalizeQuickRange({ ...range, ...patch }, range)
        : range
      ))
    })
  }

  const visibleQuickRanges = quickRanges.filter(range => range.enabled)
  const matchedQuickRange = visibleQuickRanges.find(range => range.unit === 'all'
    ? end === TODAY && (start === earliestDate || start === '')
    : end === TODAY && start === monthsAgo(quickRangeMonths(range))
  )
  const rangeKey = matchedQuickRange?.id ?? 'custom'
  const rangeTitle = matchedQuickRange ? quickRangeTitle(matchedQuickRange) : '自定义区间'
  const rangeButtonCls = (key: string) => `rounded-btn px-2 py-1 text-[11px] font-medium transition-colors ${rangeKey === key
    ? 'bg-accent/15 text-accent'
    : 'text-muted hover:bg-elevated/70 hover:text-secondary'
  }`

  const sortedTrades = useMemo(() => {
    const query = tradeSearch.trim().toLowerCase()
    return [...(result?.trades ?? [])].filter(item => {
      const matchesQuery = !query || `${item.symbol} ${item.name ?? ''}`.toLowerCase().includes(query)
      return matchesQuery && (!tradeReason || item.exit_reason === tradeReason)
    }).sort((a, b) => {
      const exitCmp = String(b.exit_date).localeCompare(String(a.exit_date))
      if (exitCmp !== 0) return exitCmp
      return String(b.entry_date).localeCompare(String(a.entry_date))
    })
  }, [result?.trades, tradeReason, tradeSearch])

  const dailyLedger = useMemo(() => [...(result?.daily_ledger ?? [])]
    .sort((a, b) => b.date.localeCompare(a.date)), [result?.daily_ledger])
  const exportDailyLedger = () => {
    if (!dailyLedger.length) return
    const quote = (value: string | number | null | undefined) => `"${String(value ?? '').replace(/"/g, '""')}"`
    const rows = [...dailyLedger].reverse().map(row => [
      row.date,
      row.buy_count,
      row.sell_count,
      row.buy_amount,
      row.sell_amount,
      row.commission,
      row.stamp_tax,
      row.slippage,
      row.commission + row.stamp_tax + row.slippage,
      row.realized_pnl,
      row.end_equity,
      row.daily_return,
    ].map(quote).join(','))
    const header = ['日期', '买入笔数', '卖出笔数', '买入成交额', '卖出成交额', '佣金', '印花税', '滑点成本', '总交易费用', '已实现盈亏', '日末权益', '日收益率']
    const blob = new Blob([`\ufeff${header.map(quote).join(',')}\n${rows.join('\n')}`], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `vnpy_daily_ledger_${start}_${end}.csv`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }
  const signalDiagnostics = useMemo(() => [...(result?.signal_diagnostics ?? [])]
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp)), [result?.signal_diagnostics])
  const exitReasons = useMemo(() => Array.from(new Set((result?.trades ?? []).map(item => item.exit_reason).filter(Boolean))).sort(), [result?.trades])

  const tradePageCount = sortedTrades.length
    ? Math.ceil(sortedTrades.length / tradePageSize)
    : 0
  const dailyPageSize = 10
  const dailyPageCount = dailyLedger.length
    ? Math.ceil(dailyLedger.length / dailyPageSize)
    : 0
  const safeDailyPage = Math.min(dailyPage, Math.max(dailyPageCount - 1, 0))
  const dailyStart = safeDailyPage * dailyPageSize
  const visibleDailyRows = dailyLedger.slice(dailyStart, dailyStart + dailyPageSize)
  const dailyEnd = Math.min(dailyStart + visibleDailyRows.length, dailyLedger.length)
  const safeTradePage = Math.min(tradePage, Math.max(tradePageCount - 1, 0))
  const tradeStart = safeTradePage * tradePageSize
  const visibleTrades = sortedTrades.slice(tradeStart, tradeStart + tradePageSize)
  const tradeEnd = Math.min(tradeStart + visibleTrades.length, sortedTrades.length)
  const detail = strategyDetail.data
  const basicFilter = (overrides.basic_filter ?? {}) as Record<string, any>
  const entrySignals = (overrides.entry_signals ?? []) as string[]
  const exitSignals = (overrides.exit_signals ?? []) as string[]

  const scoring = useMemo(() => (overrides.scoring ?? {}) as Record<string, number>, [overrides.scoring])
  const scoreMinValue = overrides.score_min == null ? '' : String(overrides.score_min)
  const scoreMaxValue = overrides.score_max == null ? '' : String(overrides.score_max)
  const stopLossPct = overrides.stop_loss == null ? '' : String(Math.abs(Number(overrides.stop_loss)) * 100)
  const takeProfitPct = overrides.take_profit == null ? '' : String(Math.abs(Number(overrides.take_profit)) * 100)
  const trailingStopPct = overrides.trailing_stop == null ? '' : String(Math.abs(Number(overrides.trailing_stop)) * 100)
  const trailingTakeProfitActivatePct = overrides.trailing_take_profit_activate == null ? '' : String(Math.abs(Number(overrides.trailing_take_profit_activate)) * 100)
  const trailingTakeProfitDrawdownPct = overrides.trailing_take_profit_drawdown == null ? '' : String(Math.abs(Number(overrides.trailing_take_profit_drawdown)) * 100)
  const maxHoldDaysValue = overrides.max_hold_days == null ? '' : String(overrides.max_hold_days)
  const targetPositionPct = Number(maxPositions) > 0 ? Number(maxExposure) / Number(maxPositions) : 0

  useEffect(() => {
    if (!editingScoring) setScoringDraft(scoringToPct(scoring))
  }, [scoring, editingScoring])

  const updateOverride = (key: string, value: any) => {
    setOverrides(prev => ({ ...prev, [key]: value }))
  }
  const updateBasicFilter = (key: string, value: any) => {
    updateOverride('basic_filter', { ...basicFilter, [key]: value })
  }
  const startScoringEdit = () => {
    setScoringDraft(scoringToPct(scoring))
    setEditingScoring(true)
  }
  const cancelScoringEdit = () => {
    setScoringDraft(scoringToPct(scoring))
    setEditingScoring(false)
  }
  const saveScoringDraft = () => {
    updateOverride('scoring', normalizePctWeights(scoringDraft))
    setEditingScoring(false)
  }
  const scoreFilterSummary = scoreMinValue !== '' && scoreMaxValue !== ''
    ? `评分 ${scoreMinValue}~${scoreMaxValue}`
    : scoreMinValue !== ''
      ? `评分 ≥${scoreMinValue}`
      : scoreMaxValue !== ''
        ? `评分 ≤${scoreMaxValue}`
        : '评分不过滤'
  const advancedSummary = detail
    ? [
        detail.params.length > 0 ? `参数 ${detail.params.length}` : '无策略参数',
        basicFilter.enabled !== false ? '过滤开' : '过滤关',
        `买点 ${entrySignals.length}`,
        `卖点 ${exitSignals.length}`,
        scoreFilterSummary,
        stopLossPct !== '' ? `止损 ${stopLossPct}%` : '止损未设',
        takeProfitPct !== '' ? `止盈 ${takeProfitPct}%` : '止盈未设',
        trailingStopPct !== '' ? `移损 ${trailingStopPct}%` : '移损未设',
        trailingTakeProfitActivatePct !== '' && trailingTakeProfitDrawdownPct !== '' ? `回撤 ${trailingTakeProfitActivatePct}-${trailingTakeProfitDrawdownPct}点` : '回撤未设',
        maxHoldDaysValue !== '' ? `最长 ${maxHoldDaysValue}天` : '不限持仓',
      ].join(' · ')
    : '选择策略后可调整参数 / 过滤 / 买卖触发器 / 评分 / 风控'
  const selectedStrategyName = detail?.name ?? strategyList.find(st => st.id === selectedStrategy)?.name ?? '未选择策略'
  const selectedStrategySource = detail?.source ?? strategyList.find(st => st.id === selectedStrategy)?.source
  const stockPoolCount = requestSymbols.length
  const stockPoolSummary = stockPoolCount > 0 ? `股票池 已限定 ${stockPoolCount} 只` : '股票池 全市场'
  const resultStartDate = result?.config?.start ?? result?.equity_curve?.[0]?.date ?? start
  const resultEndDate = result?.config?.end ?? result?.equity_curve?.[result.equity_curve.length - 1]?.date ?? end
  const resultTradeDays = result?.equity_curve?.length ?? 0
  const executionStats = (result?.stats?.execution ?? {}) as Record<string, number>
  const executionSummary = [
    ['buy_no_slot', '满仓未买'],
    ['buy_exposure', '仓位上限'],
    ['buy_score_filter', '评分过滤'],
    ['buy_limit_up', '涨停未买'],
    ['buy_suspended', '停牌未买'],
    ['sell_limit_down', '跌停阻塞'],
    ['sell_suspended', '停牌阻塞'],
    ['pending_exit', '待卖阻塞'],
  ]
    .map(([key, label]) => ({ key, label, value: Number(executionStats[key] ?? 0) }))
    .filter(item => item.value > 0)

  return (
    <div className="h-full min-h-0 overflow-hidden rounded-card border border-border bg-surface/80 grid grid-cols-1 xl:grid-cols-[18rem_minmax(0,1fr)]">
      {/* 配置面板 */}
      <section className="space-y-3 border-b xl:border-b-0 xl:border-r border-border bg-base/25 px-3 py-3 xl:overflow-y-auto">
        {highGranularity && !etf159915StockPoolStrategy && (
          <div className="rounded-btn border border-accent/25 bg-accent/5 p-2.5">
            <div className="flex items-center justify-between gap-2">
              <div>
                <div className="text-xs font-semibold text-foreground">股票池</div>
                <div className="mt-0.5 text-[10px] text-muted">从自选分组选择，或手动编辑回测标的</div>
              </div>
              <span className="rounded-full border border-accent/25 bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
                {poolSource === 'pool' ? `已选 ${poolSymbols.length} 只` : `手动 ${requestSymbols.length} 只`}
              </span>
            </div>
            <div className="mt-2"><StockPoolPicker value={symbols} onChange={setSymbols} onPoolStateChange={state => { setPoolSource(state.source); setPoolSymbols(state.symbols) }} /></div>
          </div>
        )}
        {highGranularity && etf159915StockPoolStrategy && (
          <div className="rounded-btn border border-accent/25 bg-accent/5 p-2.5">
            <div className="text-xs font-semibold text-foreground">月度股票池</div>
            <div className="mt-1.5 flex gap-1.5 text-[10px] text-secondary">
              {['2026-05', '2026-06', '2026-07'].map(month => (
                <span key={month} className="rounded border border-accent/20 bg-base px-1.5 py-0.5">{month}</span>
              ))}
            </div>
          </div>
        )}
        <div>
          <div className="mb-1.5">
            <label className="text-xs font-medium text-secondary">vn.py 分钟策略</label>
          </div>
          {/* vn.py 分钟回测说明 */}
          {highGranularity && (
            <div className="mb-2 rounded-btn border border-amber-400/30 bg-amber-400/5 px-2 py-1.5">
              <div className="flex items-start gap-1.5">
                <Zap className="h-3 w-3 text-amber-400 shrink-0 mt-px" />
                <div className="text-[10px] leading-snug text-amber-400/90">
                  <span className="font-medium">vn.py 分钟回测</span>
                  ：{vnpyStrategy ? `当前策略支持 ${vnpyStrategy.min_symbols}–${vnpyStrategy.max_symbols} 只股票，` : ''}使用本地分钟 K 与下一分钟开盘撮合。
                  <span className="text-amber-400/70"> 需要先由服务器管理员导入所选日期的本地分钟 K 数据。</span>
                  {!hasLocalMinuteData && <span className="ml-1 text-danger">当前库未检测到分钟数据。</span>}
                  <select
                    value={vnpyStrategyId}
                    onChange={event => setVnpyStrategyId(event.target.value)}
                    className="ml-2 rounded border border-amber-400/30 bg-base px-1.5 py-0.5 text-[10px] text-amber-200"
                    aria-label="vn.py 策略"
                  >
                    {(vnpyStrategies.data?.strategies ?? []).map(strategy => (
                      <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
                    ))}
                  </select>
                </div>
              </div>
              {(vnpyStrategy?.parameters.length ?? 0) > 0 && (
                <div className="mt-2 grid grid-cols-2 gap-2 border-t border-amber-400/20 pt-2">
                  {vnpyStrategy!.parameters.map(param => (
                    <VnpyStrategyParamInput
                      key={param.name}
                      param={param}
                      value={vnpyParams[param.name]}
                      onChange={value => setVnpyParams(previous => ({ ...previous, [param.name]: value }))}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
          {!highGranularity && <>
          <div className="overflow-hidden rounded-input border border-border bg-surface">
            <div className="flex border-b border-border/60 bg-base/30 p-0.5">
              {STRATEGY_GROUPS.map(group => (
                <button
                  key={group.id}
                  type="button"
                  onClick={() => setStrategyGroup(group.id)}
                  className={`flex-1 rounded-[6px] px-1.5 py-1 text-[10px] font-medium transition-colors ${strategyGroup === group.id
                    ? 'bg-accent/15 text-accent shadow-sm'
                    : 'text-muted hover:bg-elevated/70 hover:text-secondary'
                  }`}
                >
                  {group.label}
                </button>
              ))}
            </div>
            <div className="flex max-h-[128px] flex-wrap gap-1 overflow-y-auto p-1">
            {strategies.isLoading && (
              <span className="text-xs text-muted px-2 py-1">加载中…</span>
            )}
            {!strategies.isLoading && filteredStrategyList.length === 0 && (
              <span className="text-xs text-muted px-2 py-1">当前分组暂无策略</span>
            )}
            {filteredStrategyList.map(st => (
              <button
                key={st.id}
                onClick={() => setSelectedStrategy(st.id)}
                className={`px-2 py-1 rounded-btn text-[11px] border transition-all duration-150 ease-smooth cursor-pointer
                  ${selectedStrategy === st.id
                    ? 'border-accent/50 bg-accent/10 text-accent shadow-[0_0_10px_rgba(59,130,246,0.1)]'
                    : 'border-border bg-base text-secondary hover:border-accent/40'
                  }`}
              >
                <span className="font-medium">{st.name}</span>
                {st.source && st.source !== 'builtin' && (
                  <span className={`ml-1 text-[8px] px-1 py-px rounded border ${BADGE_CLS_MAP[st.source] ?? ''}`}>
                    {SRC_MAP[st.source] ?? ''}
                  </span>
                )}
              </button>
            ))}
            </div>
          </div>
          </>}
        </div>

        {selectedStrategy && strategyDetail.isLoading && (
          <div className="rounded-btn border border-border bg-surface px-2.5 py-2 text-xs text-muted">加载策略配置…</div>
        )}

        <button
          type="button"
          onClick={() => detail && setSettingsOpen(true)}
          disabled={!detail || strategyDetail.isLoading}
          className="group w-full rounded-btn border border-border bg-surface px-3 py-2.5 text-left transition-colors hover:border-accent/40 hover:bg-elevated/70 disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="flex items-center gap-2 text-xs font-semibold text-foreground">
            <SlidersHorizontal className="h-3.5 w-3.5 text-accent" />
            策略设置
            <span className="ml-auto text-[10px] font-normal text-muted group-hover:text-accent">编辑</span>
          </span>
          <span className="mt-1 flex min-w-0 items-center gap-1.5 text-[11px] font-medium text-secondary">
            <span className="truncate">{selectedStrategyName}</span>
            {selectedStrategySource && (
              <span className={`shrink-0 text-[8px] px-1 py-px rounded border ${BADGE_CLS_MAP[selectedStrategySource] ?? ''}`}>
                {SRC_MAP[selectedStrategySource] ?? selectedStrategySource}
              </span>
            )}
          </span>
          <span className="mt-1 block text-[10px] font-medium text-secondary">{stockPoolSummary}</span>
          <span className="mt-1 block text-[10px] leading-4 text-muted">{advancedSummary}</span>
        </button>

        <div className="rounded-btn border border-border bg-surface p-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1.5">
              <div className="text-xs font-medium text-foreground">回测区间</div>
              <WarmupBadge />
            </div>
            <span className="shrink-0 rounded-full border border-accent/25 bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
              {rangeTitle}
            </span>
          </div>

          <div className="mt-2 grid grid-cols-2 gap-2">
            <div>
              <label className="text-[11px] text-secondary block mb-1">开始</label>
              <div className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-1.5">
                <DatePicker
                  value={start}
                  onChange={setStart}
                  max={end || undefined}
                  placeholder="全部历史"
                  className="min-w-0"
                  buttonClassName="w-full justify-start"
                  align="left"
                />
                <input
                  type="time"
                  step={60}
                  value={startTime}
                  onChange={event => setStartTime(event.target.value)}
                  aria-label="开始时间"
                  disabled={etf159915Strategy}
                  className="min-w-0 rounded-input border border-border bg-base px-2 py-1.5 text-xs text-foreground outline-none transition-colors focus:border-accent"
                />
              </div>
            </div>
            <div>
              <label className="text-[11px] text-secondary block mb-1">结束</label>
              <div className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-1.5">
                <DatePicker
                  value={end}
                  onChange={setEnd}
                  min={start || undefined}
                  className="min-w-0"
                  buttonClassName="w-full justify-start"
                />
                <input
                  type="time"
                  step={60}
                  value={endTime}
                  onChange={event => setEndTime(event.target.value)}
                  aria-label="结束时间"
                  disabled={etf159915Strategy}
                  className="min-w-0 rounded-input border border-border bg-base px-2 py-1.5 text-xs text-foreground outline-none transition-colors focus:border-accent"
                />
              </div>
            </div>
          </div>
          {invalidMinuteRange && (
            <p className="mt-1.5 text-[11px] text-danger">
              {!startTime || !endTime ? '请选择开始和结束时间' : '同一天的开始时间不能晚于结束时间'}
            </p>
          )}

          <div className="mt-2 flex items-center gap-1">
            <div className="flex min-w-0 flex-1 rounded-input bg-base/60 p-0.5">
              {visibleQuickRanges.map(range => (
                <button
                  key={range.id}
                  type="button"
                  onClick={() => applyQuickRange(range)}
                  className={`${rangeButtonCls(range.id)} flex-1`}
                >
                  {quickRangeLabel(range)}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setRangeSettingsOpen(v => !v)}
              title="设置快捷区间"
              aria-label="设置快捷区间"
              className={`shrink-0 rounded-btn border px-2 py-1.5 transition-colors ${rangeSettingsOpen
                ? 'border-accent/40 bg-accent/10 text-accent'
                : 'border-border bg-base text-secondary hover:border-accent/40 hover:text-accent'
              }`}
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
            </button>
          </div>

          {rangeSettingsOpen && (
            <div className="mt-2 rounded-input border border-border/60 bg-base/50 p-2">
              <div className="mb-1.5 flex items-center justify-between gap-2 text-[10px] text-muted">
                <span>快捷区间</span>
                <span>月 1-120 / 年 1-10</span>
              </div>
              <div className="space-y-1.5">
                {quickRanges.map((range, index) => {
                  const limits = range.unit === 'all' ? null : QUICK_RANGE_LIMITS[range.unit]
                  return (
                    <div key={range.id} className="grid grid-cols-[3rem_1fr_4.5rem] items-center gap-1.5">
                      <label className="flex items-center gap-1 text-[11px] text-secondary">
                        <input
                          type="checkbox"
                          checked={range.enabled}
                          onChange={e => updateQuickRange(range.id, { enabled: e.target.checked })}
                          className="h-3 w-3 accent-accent"
                        />
                        {index + 1}
                      </label>
                      <select
                        value={range.unit}
                        onChange={e => updateQuickRange(range.id, { unit: e.target.value as QuickRangeUnit })}
                        className={INPUT_CLS}
                      >
                        <option value="month">月</option>
                        <option value="year">年</option>
                        <option value="all">全部</option>
                      </select>
                      <input
                        type="number"
                        min={limits?.min}
                        max={limits?.max}
                        disabled={range.unit === 'all'}
                        value={range.unit === 'all' ? '' : range.value}
                        onChange={e => updateQuickRange(range.id, { value: Number(e.target.value) })}
                        placeholder="—"
                        className={`${INPUT_CLS} ${range.unit === 'all' ? 'opacity-50' : ''}`}
                      />
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {simMode === 'position' && (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-xs font-medium text-secondary block mb-1.5">初始资金</label>
            <input type="number" value={initialCapital} onChange={e => setInitialCapital(e.target.value)}
              className={INPUT_CLS} />
          </div>
          <div>
            <label className="text-xs font-medium text-secondary block mb-1.5">买入权重</label>
            <select value={positionSizing} onChange={e => setPositionSizing(e.target.value as any)} className={INPUT_CLS}>
              <option value="equal">等权买入</option>
              <option value="score_weight">评分加权</option>
            </select>
          </div>
          <div>
            <label className="text-xs font-medium text-secondary block mb-1.5">最大持仓数</label>
            <input type="number" value={etf159915Strategy ? '1' : maxPositions} onChange={e => setMaxPositions(e.target.value)} disabled={etf159915Strategy}
              className={INPUT_CLS} />
          </div>
          <div>
            <label className="text-xs font-medium text-secondary block mb-1.5">最大总仓位(%)</label>
            <input type="number" min={0} max={100} value={maxExposure} onChange={e => setMaxExposure(e.target.value)}
              className={INPUT_CLS} />
          </div>
        </div>
        )}
        <details className="rounded-btn border border-border bg-base/40">
          <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-secondary marker:text-accent">高级成交与费用设置</summary>
          <div className="space-y-3 border-t border-border/70 p-3">
            <div className="grid grid-cols-2 gap-2">
              <div><label className="mb-1.5 block text-xs font-medium text-secondary">建仓口径</label><select value={entryFill} onChange={e => setEntryFill(e.target.value as any)} className={INPUT_CLS}><option value="open_t+1">次日开盘</option><option value="close_t">信号日收盘</option></select></div>
              <div><label className="mb-1.5 block text-xs font-medium text-secondary">清仓口径</label><select value={exitFill} onChange={e => setExitFill(e.target.value as any)} className={INPUT_CLS}><option value="close_t">信号日收盘</option><option value="open_t+1">次日开盘</option></select></div>
              <div><label className="mb-1.5 block text-xs font-medium text-secondary">佣金(万分之)</label><input type="number" min={0} value={fees} onChange={e => setFees(e.target.value)} className={INPUT_CLS} /></div>
              <div><label className="mb-1.5 block text-xs font-medium text-secondary">印花税(千分之)</label><input type="number" min={0} value={etf159915Strategy ? '0' : stampTax} onChange={e => setStampTax(e.target.value)} disabled={etf159915Strategy} className={INPUT_CLS} /></div>
              <div className="col-span-2"><label className="mb-1.5 block text-xs font-medium text-secondary">滑点(万分之)</label><input type="number" min={0} value={slippage} onChange={e => setSlippage(e.target.value)} className={INPUT_CLS} /></div>
            </div>
            {highGranularity && <div className="space-y-2"><label className="block text-xs font-medium text-secondary">技术信号价格</label><select value={etf159915Strategy ? 'raw' : signalPriceBasis} onChange={e => setSignalPriceBasis(e.target.value as 'qfq' | 'raw')} disabled={etf159915Strategy} className={INPUT_CLS}><option value="qfq">前复权（推荐）</option><option value="raw">不复权（与旧结果对照）</option></select><button type="button" aria-pressed={etf159915Strategy ? false : volumeLimitEnabled} onClick={() => setVolumeLimitEnabled(value => !value)} disabled={etf159915Strategy} className={`w-full rounded-input border px-2 py-1.5 text-left text-[11px] ${volumeLimitEnabled && !etf159915Strategy ? 'border-accent/40 bg-accent/10 text-accent' : 'border-border text-secondary'}`}>单分钟成交量 10% 限制：{etf159915Strategy ? '关闭' : volumeLimitEnabled ? '开启' : '关闭'}</button></div>}
          </div>
        </details>
        {simMode === 'position' && (
        <div className="text-[10px] leading-4 text-muted">
          单票目标约 {Number.isFinite(targetPositionPct) ? targetPositionPct.toFixed(1) : '—'}%。最大总仓位控制资金投入；剩余现金不是新增持仓名额，只有实际卖出成功才释放持仓数。
        </div>
        )}
        {highGranularity && simMode === 'position' && (
          <div className="rounded-btn border border-accent/20 bg-accent/5 px-3 py-2 text-[11px] leading-4 text-secondary">
            vn.py 组合回测会严格限制为最多 {Number(maxPositions) || 1} 只持仓。等权买入会在每个交易日开始按“可购买资金 ÷ 剩余仓位数”确定当日单仓额度，初始资金的 3% 始终保留；当天连续买入沿用该额度，只有完整卖出释放资金和仓位后才重新计算。评分加权则按策略评分分配，开盘突破策略目前以命中条件数作为评分。若同一分钟触发的买入信号超过剩余名额，先按命中条件数从多到少、再按股票代码从小到大形成候选队列；下一分钟主候选无法成交时，会在同一撮合时点继续尝试后续候选，直到填满空仓或候选耗尽。
          </div>
        )}
        {etf159915Strategy && (
          <>
            <Etf159915RuleSummary />
            <Etf159915DataReadiness query={etfReadiness} />
          </>
        )}
        {isPending ? (
          <button
            onClick={stopBacktest}
            className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-btn
              bg-danger/15 border border-danger/40 text-sm font-medium text-danger hover:bg-danger/25
              transition-colors duration-150 ease-smooth"
          >
            <Square className="h-3.5 w-3.5 fill-current" />
            停止回测
          </button>
        ) : (
          <button
            onClick={handleRun}
            disabled={vnpyStrategies.isLoading || !vnpyStrategyId || !canRunVnpy}
            className="group w-full inline-flex items-center justify-center gap-2.5 rounded-btn border border-accent/40
              bg-gradient-to-r from-accent to-blue-500 px-3 py-2.5 text-white shadow-[0_10px_24px_rgba(59,130,246,0.22)]
              transition-all duration-150 ease-smooth hover:-translate-y-0.5 hover:shadow-[0_14px_28px_rgba(59,130,246,0.28)]
              disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
          >
            <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white/18 ring-1 ring-white/25 transition-transform group-hover:scale-105">
              <Play className="h-3.5 w-3.5 translate-x-px fill-current" />
            </span>
            <span className="text-sm font-semibold tracking-wide">运行回测</span>
          </button>
        )}
        {!canRunVnpy && (
          <p className="text-center text-[11px] text-danger">
            {etfReadinessBlocked
              ? '请先解决上方数据检查中的阻断问题'
              : invalidMinuteRange
                ? !startTime || !endTime
                  ? '请选择开始和结束时间'
                  : '同一天的开始时间不能晚于结束时间'
              : poolSource === 'pool'
                ? '所选自选池为空，无法运行回测'
                : '请先选择或输入至少一只股票'}
          </p>
        )}
      </section>

      {/* 结果面板 */}
      <section className="min-w-0 space-y-3 bg-base/15 px-3 py-3 xl:overflow-y-auto">
        {result?.error && (
          <div className="text-sm text-danger bg-danger/10 border border-danger/30 rounded-btn px-3 py-2">
            {result.error}
          </div>
        )}

        {backtestTask?.error && (
          <div className="text-sm text-danger bg-danger/10 border border-danger/30 rounded-btn px-3 py-2">
            {backtestTask.error}
          </div>
        )}

        {!result && !isPending && (
          <EmptyState
            icon={FlaskConical}
            title="选择策略并开始回测"
            hint="vn.py 分钟回测使用本地 Tushare 分钟 K 数据，按策略信号、下一分钟开盘撮合、资金和持仓限制执行。"
          />
        )}

        {isPending && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-card border border-accent/40 bg-accent/10 px-4 py-2.5"
          >
            <div className="flex items-center gap-2.5">
              <span className="relative flex h-4 w-4 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/50" />
                <Loader2 className="relative h-4 w-4 animate-spin text-accent" />
              </span>
              <div className="min-w-0">
                <div className="text-xs font-medium text-accent">
                  {backtestTask?.progress
                    ? `回测中 · 第 ${backtestTask.progress.day}/${backtestTask.progress.total} 天 (${backtestTask.progress.date})`
                    : '正在重新计算回测…'}
                </div>
                <div className="mt-0.5 text-[11px] text-secondary">
                  {result ? '当前展示上次结果，完成后自动替换' : '正在加载回测数据…'}
                </div>
              </div>
              {backtestTask?.progress && (
                <span className="ml-auto shrink-0 font-mono text-sm font-semibold text-accent">
                  {((backtestTask.progress.day / backtestTask.progress.total) * 100).toFixed(0)}%
                </span>
              )}
              <button
                type="button"
                onClick={stopBacktest}
                className="inline-flex shrink-0 items-center gap-1 rounded-btn border border-danger/40 bg-danger/10 px-2 py-1 text-[11px] text-danger transition-colors hover:bg-danger/20"
              >
                <Square className="h-3 w-3 fill-current" />
                停止
              </button>
            </div>
            {backtestTask?.progress && (
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-base/60">
                <div
                  className="h-full rounded-full bg-accent transition-all duration-300 ease-out"
                  style={{ width: `${(backtestTask.progress.day / backtestTask.progress.total) * 100}%` }}
                />
              </div>
            )}
          </motion.div>
        )}

        {/* 旧全量模拟结果: 固定前瞻收益统计 (兼容历史缓存结果) */}
        {result && !result.error && result.stats && result.stats.mode === 'full' && result.stats.full_kind !== 'candidate_execution' && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="space-y-4"
          >
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-foreground">{result.strategy_info?.name ?? '策略'}</span>
              <span className="text-[10px] px-1 py-px rounded border border-accent/30 bg-accent/10 text-accent">全量模拟</span>
              <span className="text-[10px] text-secondary">持有 {result.config?.holding_days ?? 5} 天</span>
              <span className="ml-auto text-[11px] text-muted font-mono">
                {String(result.config?.start).slice(0,10)} ~ {String(result.config?.end).slice(0,10)}
              </span>
            </div>

            {/* 统计卡片 */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              <Stat label="平均收益" value={fmtPct(result.stats.avg_return)} color={statValueColor(result.stats.avg_return)} />
              <Stat label="中位数" value={fmtPct(result.stats.median_return)} color={statValueColor(result.stats.median_return)} />
              <Stat label="胜率" value={fmtPct(result.stats.win_rate)} color={statValueColor(result.stats.win_rate)} />
              <Stat label="盈亏比" value={result.stats.profit_factor != null ? Number(result.stats.profit_factor).toFixed(2) : '—'} />
              <Stat label="超额(vs基准)" value={fmtPct(result.stats.excess)} color={statValueColor(result.stats.excess)} />
              <Stat label="夏普" value={result.stats.sharpe != null ? Number(result.stats.sharpe).toFixed(2) : '—'} />
              <Stat label="最大回撤" value={fmtPct(result.stats.max_drawdown)} color={statValueColor(result.stats.max_drawdown)} />
              <Stat label="累计收益" value={fmtPct(result.stats.total_return)} color={statValueColor(result.stats.total_return)} />
            </div>

            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
              <span>候选样本 <b className="text-foreground num">{result.stats.n_candidates ?? 0}</b> (标的×信号日)</span>
              <span>信号天数 <b className="text-foreground num">{result.stats.n_days ?? 0}</b></span>
              <span>日均候选 <b className="text-foreground num">{result.stats.avg_daily_candidates ?? 0}</b></span>
              <span>最佳 <b className="text-bull num">{fmtPct(result.stats.best)}</b></span>
              <span>最差 <b className="text-bear num">{fmtPct(result.stats.worst)}</b></span>
              <span>基准(上证) <b className="text-foreground num">{fmtPct(result.stats.benchmark_return)}</b></span>
            </div>

            {/* 累计超额曲线 (复用 StrategyNavChart) */}
            {result.equity_curve.length > 1 && (
              <div className="rounded-card border border-border p-3">
                <div className="mb-2 text-xs font-medium text-secondary">累计收益曲线(日均复利)</div>
                <StrategyNavChart result={result} />
              </div>
            )}

            {/* 收益分布直方图 */}
            {Array.isArray(result.stats.return_distribution) && result.stats.return_distribution.length > 0 && (
              <div className="rounded-card border border-border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-medium text-secondary">候选标的收益分布(持有 {result.config?.holding_days ?? 5} 天)</span>
                  <span className="text-[10px] text-muted">红=正收益 · 绿=负收益</span>
                </div>
                <ReturnDistributionChart distribution={result.stats.return_distribution} />
              </div>
            )}

            <div className="text-[11px] text-muted">run_id: {result.run_id}</div>
          </motion.div>
        )}

        {result && !result.error && result.stats && !result.stats.error && (result.stats.mode !== 'full' || result.stats.full_kind === 'candidate_execution') && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="space-y-4"
          >
            {/* 策略信息 */}
            {result.strategy_info && (
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-medium text-foreground">{result.strategy_info.name}</span>
                  {result.stats.full_kind === 'candidate_execution' && (
                    <span className="text-[9px] px-1 py-px rounded border border-accent/30 bg-accent/10 text-accent">全量独立执行</span>
                  )}
                  {result.strategy_info.source && (
                    <span className={`text-[9px] px-1 py-px rounded border ${BADGE_CLS_MAP[result.strategy_info.source] ?? ''}`}>
                      {SRC_MAP[result.strategy_info.source] ?? ''}
                    </span>
                  )}
                </div>
                {result.strategy_info.stop_loss != null && (
                  <span className="text-[10px] text-secondary">止损 {fmtPct(result.strategy_info.stop_loss)}</span>
                )}
                {result.strategy_info.take_profit != null && (
                  <span className="text-[10px] text-secondary">止盈 {fmtPct(result.strategy_info.take_profit)}</span>
                )}
                {result.strategy_info.trailing_stop != null && (
                  <span className="text-[10px] text-secondary">移损 {fmtPct(result.strategy_info.trailing_stop)}</span>
                )}
                {result.strategy_info.trailing_take_profit_activate != null && result.strategy_info.trailing_take_profit_drawdown != null && (
                  <span className="text-[10px] text-secondary">回撤 {fmtPct(result.strategy_info.trailing_take_profit_activate)}-{fmtPct(result.strategy_info.trailing_take_profit_drawdown)}</span>
                )}
                {result.strategy_info.max_hold_days != null && (
                  <span className="text-[10px] text-secondary">最长 {result.strategy_info.max_hold_days} 天</span>
                )}
                {resultTradeDays > 0 && (
                  <span className="ml-auto flex items-center gap-2 text-[11px] text-muted">
                    <span className="font-mono">{String(resultStartDate).slice(0, 10)} ~ {String(resultEndDate).slice(0, 10)}</span>
                    <span>{resultTradeDays} 天</span>
                  </span>
                )}
                {result.elapsed_ms > 0 && (
                  <span className={`flex items-center gap-1 text-[11px] text-muted ${resultTradeDays > 0 ? '' : 'ml-auto'}`}>
                    <Clock className="h-3 w-3" />
                    <span>总耗时</span>
                    <span className="num">{fmtDuration(result.elapsed_ms)}</span>
                  </span>
                )}
              </div>
            )}

            {/* 统计卡片 */}
            <div className="rounded-card border border-border bg-surface p-4">
              <div className="grid grid-cols-[repeat(auto-fit,minmax(9rem,1fr))] gap-3">
                <Stat label="总收益" value={strategyReturn != null ? fmtPct(strategyReturn) : '—'}
                  color={statValueColor(strategyReturn)} />
                <Stat label="年化" value={pick('annual_return') != null ? fmtPct(pick('annual_return') as number) : '—'}
                  color={statValueColor(pick('annual_return') as number)} />
                <Stat label={isVnpyPortfolio ? '本地基准' : '同期上证'} value={benchmarkReturn != null ? fmtPct(benchmarkReturn) : isVnpyPortfolio ? '未配置' : '—'}
                  color={statValueColor(benchmarkReturn)} />
                <Stat label="超额收益" value={excessReturn != null ? fmtPct(excessReturn) : isVnpyPortfolio ? '未配置基准' : '—'}
                  color={statValueColor(excessReturn)} />
                <Stat label={<SharpeLabel />} value={pick('sharpe') != null ? Number(pick('sharpe')).toFixed(2) : '—'} />
                <Stat label="最大回撤" value={pick('max_drawdown') != null ? fmtPct(pick('max_drawdown') as number) : '—'}
                  color={statValueColor(pick('max_drawdown') as number)} />
                <Stat label="胜率" value={pick('win_rate') != null ? fmtPct(pick('win_rate') as number) : '—'} />
                <Stat label="交易数" value={pick('n_trades') != null ? String(pick('n_trades')) : '—'} />
                {result.stats.full_kind === 'candidate_execution' ? (
                  <Stat label="平均持仓" value={pick('avg_duration') != null ? `${Number(pick('avg_duration')).toFixed(1)}天` : '—'} />
                ) : (
                  <Stat label="最终权益" value={pick('final_equity') != null ? fmtPrice(pick('final_equity') as number) : '—'} />
                )}
              </div>
            </div>

            {isVnpyPortfolio && (
              <div className="rounded-card border border-amber-400/25 bg-amber-400/5 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-amber-200">分钟组合执行摘要</span>
                  <span className="text-[10px] text-amber-200/70">基准未配置，超额收益不计算</span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px] text-secondary md:grid-cols-4">
                  {isEtfStockPoolResult ? (
                    <span className="md:col-span-2">月度股票池 <b className="font-mono text-foreground">{monthlyPoolSummary || '—'}</b></span>
                  ) : (
                    <span>股票池 <b className="font-mono text-foreground">{pick('symbols_requested') ?? 0}</b></span>
                  )}
                  <span>回放交易日 <b className="font-mono text-foreground">{pick('trading_days') ?? 0}</b></span>
                  <span>订单成交 <b className="font-mono text-foreground">{pick('order_fill_count') ?? 0}</b></span>
                  <span>期末持仓 <b className="font-mono text-foreground">{pick('open_position_count') ?? 0}</b></span>
                  <span>佣金 <b className="font-mono text-foreground">{fmtPrice(Number(pick('commission') ?? 0))}</b></span>
                  <span>印花税 <b className="font-mono text-foreground">{fmtPrice(Number(pick('stamp_tax') ?? 0))}</b></span>
                  <span>滑点成本 <b className="font-mono text-foreground">{fmtPrice(Number(pick('slippage_cost') ?? 0))}</b></span>
                  <span>拒单 <b className="font-mono text-foreground">{pick('rejection_count') ?? 0}</b></span>
                  <span>平均单笔 <b className="font-mono text-foreground">{pick('avg_trade_return') != null ? fmtPct(pick('avg_trade_return') as number) : '—'}</b></span>
                  <span>盈亏比 <b className="font-mono text-foreground">{pick('profit_factor') != null ? Number(pick('profit_factor')).toFixed(2) : '—'}</b></span>
                  <span>总成本 <b className="font-mono text-foreground">{fmtPrice(Number(pick('total_cost') ?? 0))}</b></span>
                </div>
              </div>
            )}
            {!!result.warnings?.length && (
              <div className="rounded-btn border border-warning/30 bg-warning/[0.06] px-3 py-2 text-xs leading-5 text-warning">
                {result.warnings.map(warning => <p key={warning}>{warning}</p>)}
              </div>
            )}

            {executionSummary.length > 0 && (
              <div className="rounded-card border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-[11px] leading-5 text-secondary">
                <span className="font-medium text-amber-300">成交约束：</span>
                {executionSummary.map((item, index) => (
                  <span key={item.key} className="ml-2">
                    {index > 0 ? '· ' : ''}{item.label} <span className="font-mono text-foreground">{item.value}</span> 次
                  </span>
                ))}
              </div>
            )}

            {/* 净值曲线 */}
            {result.equity_curve.length > 0 && (
              <div className="rounded-card border border-border overflow-hidden">
                <StrategyNavChart result={result} />
              </div>
            )}

            {Array.isArray(result.stats.return_distribution) && result.stats.return_distribution.length > 0 && (
              <div className="rounded-card border border-border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-xs font-medium text-secondary">独立候选交易收益分布</span>
                  <span className="text-[10px] text-muted">红=正收益 · 绿=负收益</span>
                </div>
                <ReturnDistributionChart distribution={result.stats.return_distribution} />
              </div>
            )}

            {(dailyLedger.length > 0 || result.trades.length > 0 || signalDiagnostics.length > 0 || (result.positions?.length ?? 0) > 0) && (
              <div className="rounded-card border border-border overflow-hidden">
                <div className="flex items-center gap-1 border-b border-border px-4 pt-2">
                  {([
                    'daily',
                    'trades',
                    'signals',
                    'positions',
                    ...(isEtfResult ? ['execution' as const] : []),
                  ] as const).map(t => (
                    <button
                      key={t}
                      onClick={() => setResultTab(t)}
                      className={`px-3 py-1.5 text-xs font-medium border-b-2 transition-colors cursor-pointer ${
                        resultTab === t
                          ? 'border-accent text-accent'
                          : 'border-transparent text-secondary hover:text-foreground'
                      }`}
                    >
                      {t === 'daily'
                        ? `每日账本 (${dailyLedger.length})`
                        : t === 'trades'
                          ? `交易明细 (${sortedTrades.length})`
                          : t === 'signals'
                            ? `信号诊断 (${signalDiagnostics.length})`
                            : t === 'positions'
                              ? `期末持仓 (${result.positions?.length ?? 0})`
                              : `执行追踪 (${signalDiagnostics.length})`}
                    </button>
                  ))}
                  {resultTab === 'daily' && dailyLedger.length > 0 && (
                    <button
                      type="button"
                      onClick={exportDailyLedger}
                      title="导出每日账本 CSV"
                      className="ml-auto mb-1.5 flex items-center gap-1 rounded-btn border border-border bg-surface px-2 py-1 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent"
                    >
                      <Download size={13} />
                      导出
                    </button>
                  )}
                </div>

                {resultTab === 'daily' && (
                  <div>
                    <div className="overflow-x-auto">
                    <table className="w-full min-w-[1080px] text-sm text-foreground">
                      <thead className="bg-elevated">
                        <tr className="text-left text-secondary">
                          <th className="px-3 py-2.5 font-medium">日期 / 成交</th>
                          <th className="px-3 py-2.5 font-medium text-right">买入金额</th>
                          <th className="px-3 py-2.5 font-medium text-right">卖出金额</th>
                          <th className="px-3 py-2.5 font-medium text-right">交易费用</th>
                          <th className="px-3 py-2.5 font-medium text-right">已实现盈亏</th>
                          <th className="px-3 py-2.5 font-medium text-right">日末权益</th>
                          <th className="px-3 py-2.5 font-medium text-right">日收益</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visibleDailyRows.map(row => (
                          <Fragment key={row.date}>
                            <tr onClick={() => setDailyExpanded(value => value === row.date ? null : row.date)} className="cursor-pointer border-t border-border hover:bg-elevated/50 transition-colors">
                              <td className="px-3 py-2.5 whitespace-nowrap"><div className="font-mono text-foreground">{row.date}</div><div className="mt-0.5 text-[11px] text-muted">买 {row.buy_count} / 卖 {row.sell_count} · 点击展开</div></td>
                              <td className="px-3 py-2.5 text-right num">{fmtMoney(row.buy_amount)}</td>
                              <td className="px-3 py-2.5 text-right num">{fmtMoney(row.sell_amount)}</td>
                              <td className="px-3 py-2.5 text-right num text-secondary">{fmtMoney(row.commission + row.stamp_tax + row.slippage)}</td>
                              <td className={`px-3 py-2.5 text-right num font-semibold ${priceColorClass(row.realized_pnl)}`}>{fmtSignedMoney(row.realized_pnl)}</td>
                              <td className="px-3 py-2.5 text-right num">{fmtMoney(row.end_equity)}</td>
                              <td className={`px-3 py-2.5 text-right num font-semibold ${priceColorClass(row.daily_return)}`}>{fmtPct(row.daily_return)}</td>
                            </tr>
                            {dailyExpanded === row.date && <tr className="border-t border-border bg-base/40"><td colSpan={7} className="px-5 py-3"><div className="mb-2 text-xs font-medium text-secondary">当日逐笔成交</div><div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">{row.fills.length ? row.fills.map((fill, index) => <div key={`${fill.symbol}-${fill.entry_date}-${index}`} className="rounded-btn border border-border/70 bg-surface px-3 py-2 text-xs"><div className="flex justify-between gap-2"><span className="font-mono text-foreground">{fill.symbol}</span><span className={isVnpyLongDirection(fill.direction) ? 'text-accent' : 'text-secondary'}>{isVnpyLongDirection(fill.direction) ? '买入' : '卖出'}</span></div><div className="mt-1 text-secondary">{fill.name || '名称未知'} · {String(fill.entry_date).slice(11, 16)} · {fmtShares(fill.shares)} 股</div><div className="mt-1 num text-foreground">{fmtMoney(fill.entry_value)}</div></div>) : <span className="text-xs text-muted">当日无成交</span>}</div></td></tr>}
                          </Fragment>
                        ))}
                      </tbody>
                    </table>
                    </div>
                    {dailyLedger.length > 0 && (
                      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-2 text-xs text-muted">
                        <span>
                          显示 {dailyStart + 1}-{dailyEnd} 天 / 共 {dailyLedger.length} 天，每页 10 天
                        </span>
                        <div className="flex flex-wrap items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setDailyPage(p => Math.max(0, p - 1))}
                            disabled={safeDailyPage <= 0}
                            className="rounded-btn border border-border bg-surface px-2.5 py-1 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            上一页
                          </button>
                          <span className="num text-secondary">
                            {safeDailyPage + 1} / {dailyPageCount}
                          </span>
                          <button
                            type="button"
                            onClick={() => setDailyPage(p => Math.min(dailyPageCount - 1, p + 1))}
                            disabled={safeDailyPage >= dailyPageCount - 1}
                            className="rounded-btn border border-border bg-surface px-2.5 py-1 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            下一页
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {resultTab === 'trades' && (
                  <div>
                    <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5"><input value={tradeSearch} onChange={event => { setTradeSearch(event.target.value); setTradePage(0) }} placeholder="搜索代码或名称" className="w-48 rounded-btn border border-border bg-base px-2.5 py-1.5 text-xs outline-none focus:border-accent" /><select value={tradeReason} onChange={event => { setTradeReason(event.target.value); setTradePage(0) }} className="rounded-btn border border-border bg-base px-2.5 py-1.5 text-xs text-secondary outline-none focus:border-accent"><option value="">全部退出原因</option>{exitReasons.map(reason => <option key={reason} value={reason}>{reason}</option>)}</select></div>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[960px] text-sm text-foreground">
                      <thead className="bg-elevated">
                        <tr className="text-left text-secondary">
                          <th className="px-4 py-2.5 font-medium">股票代码</th>
                          <th className="px-4 py-2.5 font-medium">股票名称</th>
                          <th className="px-4 py-2.5 font-medium">买入</th>
                          <th className="px-4 py-2.5 font-medium">卖出</th>
                          <th className="px-4 py-2.5 font-medium text-right">仓位 / 手数</th>
                          <th className="px-4 py-2.5 font-medium text-right">单票盈亏</th>
                          <th className="px-4 py-2.5 font-medium text-right">持仓</th>
                          <th className="px-4 py-2.5 font-medium">原因</th>
                        </tr>
                      </thead>
                      <tbody>
                        {visibleTrades.map((t: StrategyBacktestTrade, i: number) => (
                          <tr key={`${t.symbol}-${t.entry_date}-${tradeStart + i}`} onClick={() => setSelectedTrade(t)} className="cursor-pointer border-t border-border hover:bg-elevated/50 transition-colors group">
                            <td className="px-4 py-2.5 font-mono text-foreground">{t.symbol}</td>
                            <td className="px-4 py-2.5 font-medium text-foreground group-hover:text-accent transition-colors">{t.name || '名称未知'}</td>
                            <td className="px-4 py-2.5">
                              <TradeLegCell trade={t} side="buy" />
                            </td>
                            <td className="px-4 py-2.5">
                              <TradeLegCell trade={t} side="sell" />
                            </td>
                            <td className="px-4 py-2.5 text-right">
                              <div className="num text-foreground">{fmtPct(t.position_pct, 2)}</div>
                              <div className="mt-0.5 text-[11px] text-muted">
                                <span className="num">{fmtLots(t.lots)}</span> 手
                                <span className="ml-1 num">{fmtShares(t.shares)}</span> 股
                              </div>
                            </td>
                            <td className={`px-4 py-2.5 text-right num ${priceColorClass(t.pnl_amount ?? t.pnl_pct)}`}>
                              <div>{fmtSignedMoney(t.pnl_amount)}</div>
                              <div className="mt-0.5 text-[11px]">{fmtPct(t.pnl_pct)}</div>
                            </td>
                            <td className="px-4 py-2.5 text-right num text-secondary">
                              <div>{t.duration} 个交易日</div>
                              <div className="mt-0.5 text-[11px] text-muted">{t.duration_minutes ?? 0} 个有效分钟</div>
                              {!!t.blocked_exit_days && <div className="mt-0.5 text-[11px] text-amber-400">阻塞 {t.blocked_exit_days} 天</div>}
                            </td>
                            <td className="px-4 py-2.5"><ExitReasonBadge reason={t.exit_reason} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {sortedTrades.length > 0 && (
                      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-2 text-xs text-muted">
                        <span>
                          显示 {tradeStart + 1}-{tradeEnd} 条 / 共 {sortedTrades.length} 条
                        </span>
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="flex items-center gap-1.5">
                            <span>每页</span>
                            <select
                              value={tradePageSize}
                              onChange={e => {
                                setTradePageSize(Number(e.target.value))
                                setTradePage(0)
                              }}
                              className="rounded-btn border border-border bg-surface px-2 py-1 text-xs text-secondary focus:outline-none focus:border-accent"
                            >
                              {TRADE_PAGE_SIZE_OPTIONS.map(size => (
                                <option key={size} value={size}>{size}</option>
                              ))}
                            </select>
                            <span>条</span>
                          </label>
                          <button
                            type="button"
                            onClick={() => setTradePage(p => Math.max(0, p - 1))}
                            disabled={safeTradePage <= 0}
                            className="rounded-btn border border-border bg-surface px-2.5 py-1 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            上一页
                          </button>
                          <span className="num text-secondary">
                            {safeTradePage + 1} / {tradePageCount}
                          </span>
                          <button
                            type="button"
                            onClick={() => setTradePage(p => Math.min(tradePageCount - 1, p + 1))}
                            disabled={safeTradePage >= tradePageCount - 1}
                            className="rounded-btn border border-border bg-surface px-2.5 py-1 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent disabled:cursor-not-allowed disabled:opacity-45"
                          >
                            下一页
                          </button>
                        </div>
                      </div>
                    )}
                  </div></div>
                )}

                {resultTab === 'signals' && (
                  <div className="overflow-x-auto"><table className="w-full min-w-[920px] text-sm">
                    <thead className="bg-elevated">
                      <tr className="text-left text-secondary">
                        <th className="px-4 py-2.5 font-medium">触发时间</th><th className="px-4 py-2.5 font-medium">股票代码</th><th className="px-4 py-2.5 font-medium">股票名称</th><th className="px-4 py-2.5 font-medium">方向 / 条件</th><th className="px-4 py-2.5 font-medium">执行状态</th><th className="px-4 py-2.5 font-medium">成交或拒单原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      {signalDiagnostics.map(item => <tr key={item.id} className="border-t border-border hover:bg-elevated/50"><td className="px-4 py-2 font-mono text-xs text-secondary whitespace-nowrap">{item.timestamp}</td><td className="px-4 py-2 font-mono">{item.symbol}</td><td className="px-4 py-2">{item.name || '名称未知'}</td><td className="px-4 py-2"><div className={isVnpyLongDirection(item.direction) ? 'text-accent' : 'text-secondary'}>{isVnpyLongDirection(item.direction) ? '买入' : '卖出'}</div><div className="mt-0.5 text-xs text-muted">{item.conditions.join('；') || item.reason}</div></td><td className="px-4 py-2"><span className={`inline-flex whitespace-nowrap rounded border px-1.5 py-0.5 text-xs ${item.status === 'filled' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400' : item.status === 'rejected' ? 'border-red-500/30 bg-red-500/10 text-red-400' : 'border-border text-secondary'}`}>{item.status === 'filled' ? '已成交' : item.status === 'rejected' ? '已拒单' : item.status === 'queued' ? '已排队' : '已触发'}</span></td><td className="px-4 py-2 text-xs text-secondary whitespace-nowrap">{vnpySignalExecutionText(item)}</td></tr>)}
                    </tbody>
                  </table></div>
                )}

                {resultTab === 'positions' && (
                  <div className="overflow-x-auto"><table className="w-full min-w-[1040px] text-sm"><thead className="bg-elevated"><tr className="text-left text-secondary"><th className="px-4 py-2.5 font-medium">股票代码</th><th className="px-4 py-2.5 font-medium">股票名称</th><th className="px-4 py-2.5 font-medium text-right">持仓股数</th><th className="px-4 py-2.5 font-medium text-right">成本 / 收盘</th><th className="px-4 py-2.5 font-medium text-right">市值 / 仓位</th><th className="px-4 py-2.5 font-medium text-right">浮动盈亏</th><th className="px-4 py-2.5 font-medium">建仓 / 持有</th></tr></thead><tbody>{(result.positions ?? []).map(item => <tr key={item.symbol} className="border-t border-border hover:bg-elevated/50"><td className="px-4 py-2 font-mono">{item.symbol}</td><td className="px-4 py-2 font-medium">{item.name || '名称未知'}</td><td className="px-4 py-2 text-right num">{fmtShares(item.volume)}</td><td className="px-4 py-2 text-right num"><div>{fmtPrice(item.average_cost)}</div><div className="mt-0.5 text-xs text-muted">{fmtPrice(item.mark_price)}</div></td><td className="px-4 py-2 text-right num"><div>{fmtMoney(item.market_value)}</div><div className="mt-0.5 text-xs text-muted">{fmtPct(item.position_pct)}</div></td><td className={`px-4 py-2 text-right num ${priceColorClass(item.unrealized_pnl)}`}><div>{fmtSignedMoney(item.unrealized_pnl)}</div><div className="mt-0.5 text-xs">{fmtPct(item.unrealized_pnl_pct)}</div></td><td className="px-4 py-2 text-xs text-secondary"><div>{item.entry_date || '—'}</div><div className="mt-0.5">{item.holding_days} 个交易日 · {item.holding_minutes} 个有效分钟</div></td></tr>)}</tbody></table>{!(result.positions?.length) && <div className="p-8 text-center text-sm text-muted">回测结束时没有未平仓持仓。</div>}</div>
                )}

                {resultTab === 'execution' && isEtfResult && (
                  <Etf159915ExecutionTrace result={result} />
                )}
              </div>
            )}

            <div className="text-[11px] text-muted">
              run_id: {result.run_id}
            </div>
          </motion.div>
        )}
      </section>

      {settingsOpen && detail && (
        <>
          <motion.button
            type="button"
            aria-label="关闭高级策略设置"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            onClick={() => setSettingsOpen(false)}
            className="fixed inset-0 z-50 bg-black/45 backdrop-blur-[1px]"
          />
          <motion.aside
            initial={{ x: 32, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="fixed inset-y-0 right-0 z-[60] flex w-full max-w-3xl flex-col border-l border-border bg-base shadow-2xl"
          >
            <div className="border-b border-border px-4 py-3">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-foreground">高级策略设置</span>
                    <span className={`text-[9px] px-1 py-px rounded border ${BADGE_CLS_MAP[detail.source] ?? ''}`}>
                      {SRC_MAP[detail.source] ?? ''}
                    </span>
                  </div>
                  <div className="mt-1 truncate text-xs text-secondary">{detail.name}</div>
                  <div className="mt-0.5 text-[10px] leading-4 text-muted">{advancedSummary}</div>
                </div>
                <button
                  type="button"
                  onClick={() => setSettingsOpen(false)}
                  className="rounded-btn border border-border bg-surface p-1.5 text-muted transition-colors hover:border-accent/40 hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="mt-3 flex gap-1 overflow-x-auto">
                {ADVANCED_TABS.map(tab => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setSettingsTab(tab.id)}
                    className={`shrink-0 rounded-btn border px-3 py-1.5 text-xs transition-colors ${settingsTab === tab.id
                      ? 'border-accent/50 bg-accent/10 text-accent'
                      : 'border-border bg-surface text-secondary hover:border-accent/40 hover:text-foreground'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
              <div className="mb-4 rounded-btn border border-accent/25 bg-accent/5 px-3 py-2.5 text-[11px] leading-5 text-secondary">
                <div className="font-medium text-foreground">触发 / 成交 / 仓位关系</div>
                <div className="mt-1">触发器决定什么时候产生买卖信号；评分只在多个买点同时出现时排序。</div>
                <div>成交口径可分别设置建仓/清仓：默认建仓次日开盘（避免未来函数）、清仓当日收盘（持仓中可盘中/收盘卖）。</div>
                <div>退出优先级：止损/移动止损 &gt; 卖点信号 &gt; 到期平仓；到期只作兜底，不抢占卖点或风控。</div>
                <div>最大持仓数控制同时持股数量，最大总仓位控制资金投入比例；剩余现金不等于可新增持仓名额。</div>
              </div>

              {settingsTab === 'range' && (
                <ConfigSection title="回测范围">
                  <StockPoolPicker
                    value={symbols}
                    onChange={setSymbols}
                    onPoolStateChange={state => {
                      setPoolSource(state.source)
                      setPoolSymbols(state.symbols)
                    }}
                  />
                  <div className="text-[11px] leading-5 text-muted">默认全市场回测，由基础过滤、策略条件和买卖触发器筛选；需要单票调试或自选池回测时再限定股票池。</div>
                </ConfigSection>
              )}

              {settingsTab === 'params' && (
                <ConfigSection title="策略参数" hint="自动限制 min/max">
                  {detail.params.length > 0 ? (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      {detail.params.map(param => (
                        <StrategyParamInput
                          key={param.id}
                          param={param}
                          value={strategyParams[param.id]}
                          onChange={value => setStrategyParams(prev => ({ ...prev, [param.id]: value }))}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="text-xs text-muted">当前策略没有可调参数。</div>
                  )}
                </ConfigSection>
              )}

              {settingsTab === 'filter' && (
                <ConfigSection title="基础过滤" hint="用于候选池">
                  <label className="flex items-center gap-2 text-xs text-secondary">
                    <input
                      type="checkbox"
                      checked={basicFilter.enabled !== false}
                      onChange={e => updateBasicFilter('enabled', e.target.checked)}
                    />
                    启用基础过滤
                  </label>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {BASIC_FILTER_FIELDS.map(field => {
                      const scale = field.scale ?? 1
                      const value = basicFilter[field.key] == null ? '' : Number(basicFilter[field.key]) / scale
                      return (
                        <label key={field.key} className="block">
                          <span className="mb-1 block text-[11px] text-secondary">{field.label}({field.unit})</span>
                          <input
                            type="number"
                            value={value}
                            min={0}
                            step={field.unit === '%' ? 0.1 : 0.01}
                            onChange={e => {
                              const n = numOrNull(e.target.value)
                              updateBasicFilter(field.key, n == null ? null : n * scale)
                            }}
                            className={INPUT_CLS}
                          />
                        </label>
                      )
                    })}
                  </div>
                  <label className="flex items-center gap-2 text-xs text-secondary">
                    <input
                      type="checkbox"
                      checked={!!basicFilter.exclude_st}
                      onChange={e => updateBasicFilter('exclude_st', e.target.checked)}
                    />
                    排除 ST / 退市
                  </label>
                  <div className="flex flex-wrap gap-1.5">
                    {BOARD_OPTIONS.map(board => {
                      const boards = Array.isArray(basicFilter.boards) ? basicFilter.boards : []
                      const checked = boards.includes(board)
                      return (
                        <button
                          key={board}
                          type="button"
                          onClick={() => updateBasicFilter('boards', checked ? boards.filter((b: string) => b !== board) : [...boards, board])}
                          className={`rounded-btn border px-2.5 py-1.5 text-[11px] transition-colors ${checked ? 'border-accent/50 bg-accent/10 text-accent' : 'border-border bg-base text-muted hover:border-accent/40'}`}
                        >
                          {board}
                        </button>
                      )
                    })}
                  </div>
                </ConfigSection>
              )}

              {settingsTab === 'entry' && (
                <ConfigSection
                  title="买入触发器"
                  hint="任一买点满足即可进入候选"
                  actions={<SignalTriggerActions kind="entry" signals={entrySignals} onChange={next => updateOverride('entry_signals', next)} />}
                >
                  <SignalPicker
                    signals={entrySignals}
                    onChange={next => updateOverride('entry_signals', next)}
                    kind="entry"
                  />
                </ConfigSection>
              )}

              {settingsTab === 'exit' && (
                <ConfigSection
                  title="卖出触发器"
                  hint="任一卖点满足即触发卖出"
                  actions={<SignalTriggerActions kind="exit" signals={exitSignals} onChange={next => updateOverride('exit_signals', next)} />}
                >
                  <SignalPicker
                    signals={exitSignals}
                    onChange={next => updateOverride('exit_signals', next)}
                    kind="exit"
                  />
                </ConfigSection>
              )}

              {settingsTab === 'scoring' && (
                <ConfigSection title="评分权重" hint="临时拖动滑块，保存时统一归权">
                  {Object.entries(scoring).length > 0 ? (() => {
                    const visibleWeights = editingScoring ? scoringDraft : scoringToPct(scoring)
                    const total = Object.values(visibleWeights).reduce((a, b) => a + b, 0)
                    return (
                      <div className="space-y-3">
                        <div className="space-y-2">
                          {Object.keys(scoring).map(key => (
                            <ScoringWeightRow
                              key={key}
                              name={key}
                              weight={visibleWeights[key] ?? 0}
                              pct={visibleWeights[key] ?? 0}
                              editing={editingScoring}
                              onChange={value => setScoringDraft(prev => ({ ...prev, [key]: Math.max(0, value) }))}
                            />
                          ))}
                        </div>
                        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/40 pt-2">
                          <div className="text-[10px] text-muted">
                            总和 <span className={`font-mono text-xs font-medium ${editingScoring && total !== 100 ? 'text-amber-400' : 'text-emerald-400'}`}>{editingScoring ? total : 100}</span>
                            <span className="ml-1 text-muted/70">保存时自动归一化计算</span>
                          </div>
                          <div className="flex items-center gap-2">
                            {editingScoring && (
                              <button
                                type="button"
                                onClick={cancelScoringEdit}
                                className="rounded-btn border border-border bg-base px-2.5 py-1 text-[11px] text-secondary transition-colors hover:border-accent/40 hover:text-foreground"
                              >
                                取消
                              </button>
                            )}
                            <button
                              type="button"
                              onClick={editingScoring ? saveScoringDraft : startScoringEdit}
                              className="rounded-btn border border-amber-400/40 bg-amber-400/10 px-2.5 py-1 text-[11px] text-amber-400 transition-colors hover:bg-amber-400/15"
                            >
                              {editingScoring ? '保存归权' : '调整权重'}
                            </button>
                          </div>
                        </div>
                      </div>
                    )
                  })() : (
                    <div className="text-xs text-muted">当前策略没有评分权重。</div>
                  )}
                  <div className="border-t border-border/40 pt-3">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <span className="text-[11px] font-medium text-secondary">评分过滤</span>
                      <span className="text-[10px] text-muted">留空 = 不过滤；命中范围后按评分从高到低买入</span>
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <label className="block">
                        <span className="mb-1 block text-[11px] text-secondary">最小评分</span>
                        <input
                          type="number"
                          value={scoreMinValue}
                          min={0}
                          max={100}
                          step={1}
                          placeholder="不限"
                          onChange={e => {
                            const n = numOrNull(e.target.value)
                            updateOverride('score_min', n == null ? null : clamp(n, 0, 100))
                          }}
                          className={INPUT_CLS}
                        />
                      </label>
                      <label className="block">
                        <span className="mb-1 block text-[11px] text-secondary">最大评分</span>
                        <input
                          type="number"
                          value={scoreMaxValue}
                          min={0}
                          max={100}
                          step={1}
                          placeholder="不限"
                          onChange={e => {
                            const n = numOrNull(e.target.value)
                            updateOverride('score_max', n == null ? null : clamp(n, 0, 100))
                          }}
                          className={INPUT_CLS}
                        />
                      </label>
                    </div>
                    <div className="mt-2 text-[10px] leading-4 text-muted">例如最小值 71 表示只把评分 ≥ 71 的股票放入下一交易日买入预选池。</div>
                  </div>
                </ConfigSection>
              )}

              {settingsTab === 'risk' && (
                <ConfigSection title="风控">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-secondary">止损(%)</span>
                      <input
                        type="number"
                        value={stopLossPct}
                        min={0}
                        max={99}
                        step={0.5}
                        onChange={e => {
                          const n = numOrNull(e.target.value)
                          updateOverride('stop_loss', n == null ? null : -Math.abs(n) / 100)
                        }}
                        className={INPUT_CLS}
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-secondary">止盈(%)</span>
                      <input
                        type="number"
                        value={takeProfitPct}
                        min={1}
                        max={500}
                        step={0.5}
                        onChange={e => {
                          const n = numOrNull(e.target.value)
                          updateOverride('take_profit', n == null ? null : clamp(Math.abs(n), 1, 500) / 100)
                        }}
                        className={INPUT_CLS}
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-secondary">移动止损(%)</span>
                      <input
                        type="number"
                        value={trailingStopPct}
                        min={0.5}
                        max={50}
                        step={0.5}
                        onChange={e => {
                          const n = numOrNull(e.target.value)
                          updateOverride('trailing_stop', n == null ? null : -clamp(Math.abs(n), 0.5, 50) / 100)
                        }}
                        className={INPUT_CLS}
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-secondary">回撤止盈启动(%)</span>
                      <input
                        type="number"
                        value={trailingTakeProfitActivatePct}
                        min={1}
                        max={200}
                        step={0.5}
                        onChange={e => {
                          const n = numOrNull(e.target.value)
                          const next = n == null ? null : clamp(Math.abs(n), 1, 200) / 100
                          updateOverride('trailing_take_profit_activate', next)
                          const drawdown = numOrNull(trailingTakeProfitDrawdownPct)
                          if (next != null && drawdown != null && drawdown / 100 > next) {
                            updateOverride('trailing_take_profit_drawdown', next)
                          }
                        }}
                        className={INPUT_CLS}
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-secondary">回撤止盈回撤(点)</span>
                      <input
                        type="number"
                        value={trailingTakeProfitDrawdownPct}
                        min={0.5}
                        max={50}
                        step={0.5}
                        onChange={e => {
                          const n = numOrNull(e.target.value)
                          const activate = numOrNull(trailingTakeProfitActivatePct)
                          const maxValue = activate == null ? 50 : Math.min(50, Math.abs(activate))
                          updateOverride('trailing_take_profit_drawdown', n == null ? null : clamp(Math.abs(n), 0.5, maxValue) / 100)
                        }}
                        className={INPUT_CLS}
                      />
                    </label>
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-secondary">最长持仓(天)</span>
                      <input
                        type="number"
                        value={maxHoldDaysValue}
                        min={1}
                        step={1}
                        onChange={e => {
                          const n = numOrNull(e.target.value)
                          updateOverride('max_hold_days', n == null ? null : Math.max(1, Math.round(n)))
                        }}
                        className={INPUT_CLS}
                      />
                    </label>
                  </div>
                </ConfigSection>
              )}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
              <button
                type="button"
                onClick={() => resetConfigFromDetail(detail)}
                className="rounded-btn border border-border bg-surface px-3 py-1.5 text-xs text-secondary transition-colors hover:border-accent/40 hover:text-accent"
              >
                恢复默认
              </button>
              <button
                type="button"
                onClick={() => setSettingsOpen(false)}
                className="rounded-btn bg-accent px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent/90"
              >
                完成
              </button>
            </div>
          </motion.aside>
        </>
      )}

      <TradeKlineModal trade={selectedTrade} onClose={() => setSelectedTrade(null)} />
    </div>
  )
}
