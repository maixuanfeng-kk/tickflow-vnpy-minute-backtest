import { AlertTriangle, CheckCircle2, ChevronDown, CircleX, Database, RefreshCw } from 'lucide-react'
import type { ReactNode } from 'react'
import type { StrategyBacktestResult, Etf159915Readiness } from '@/lib/api'
import { fmtPrice } from '@/lib/format'
import {
  ETF_159915_EXTENSION,
  buildEtf159915ExecutionRows,
} from './etf159915'

type ReadinessQuery = {
  data?: Etf159915Readiness
  error?: unknown
  isLoading: boolean
  isError: boolean
  refetch: () => unknown
}

function RuleLine({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-t border-border/60 py-2 text-[11px]">
      <span className="shrink-0 text-secondary">{label}</span>
      <span className="text-right text-foreground">{children}</span>
    </div>
  )
}

export function Etf159915RuleSummary() {
  return (
    <details className="group rounded-btn border border-accent/25 bg-accent/5">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5 text-xs font-medium text-foreground">
        <span className="flex min-w-0 items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-accent/30 bg-accent/10 text-[10px] text-accent">159</span>
          <span className="truncate">159915 策略规则</span>
          <span className="rounded border border-accent/25 px-1.5 py-0.5 text-[10px] font-normal text-accent">只读</span>
        </span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="space-y-0 px-3 pb-3">
        <RuleLine label="标的">{ETF_159915_EXTENSION.symbol}</RuleLine>
        <RuleLine label="规则版本">{ETF_159915_EXTENSION.ruleVersion}</RuleLine>
        <RuleLine label="行情与成交">原始 1 分钟线 · 收盘判信号 · 下一根真实分钟开盘成交</RuleLine>
        <RuleLine label="仓位与制度">97% 可用资金 · 单仓 · T+1 · 印花税 0</RuleLine>
        <RuleLine label="买入规则">
          <span className="font-mono">{ETF_159915_EXTENSION.entryRules.join(' · ')}</span>
        </RuleLine>
        <RuleLine label="卖出规则">
          <span className="font-mono">{ETF_159915_EXTENSION.exitRules.join(' · ')}</span>
        </RuleLine>
        <RuleLine label="优先级">
          <span className="font-mono">{ETF_159915_EXTENSION.priorities.join('；')}</span>
        </RuleLine>
      </div>
    </details>
  )
}

function CoverageLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-2 text-[10px]">
      <span className="truncate text-muted">{label}</span>
      <span className="shrink-0 font-mono text-secondary">{value}</span>
    </div>
  )
}

export function Etf159915DataReadiness({ query }: { query: ReadinessQuery }) {
  const data = query.data
  const refetch = () => { void query.refetch() }
  const statusClass = data?.ready
    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
    : 'border-red-500/30 bg-red-500/10 text-red-400'
  return (
    <section className="rounded-btn border border-border bg-surface/70 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2 text-xs font-medium text-foreground">
          <Database className="h-3.5 w-3.5 shrink-0 text-accent" />
          <span>数据检查</span>
        </div>
        {data && (
          <span className={'inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] ' + statusClass}>
            {data.ready ? <CheckCircle2 className="h-3 w-3" /> : <CircleX className="h-3 w-3" />}
            {data.ready ? '可以运行' : '禁止运行'}
          </span>
        )}
      </div>

      {query.isLoading && (
        <div className="mt-2 text-[11px] text-secondary">正在检查日线、分钟线覆盖…</div>
      )}
      {query.isError && (
        <div className="mt-2 flex items-center justify-between gap-2 rounded border border-red-500/25 bg-red-500/5 px-2 py-1.5 text-[11px] text-red-300">
          <span>数据检查请求失败，暂时禁止运行。</span>
          <button type="button" onClick={refetch} className="inline-flex shrink-0 items-center gap-1 rounded border border-red-500/30 px-1.5 py-1 text-[10px] text-red-300 hover:bg-red-500/10">
            <RefreshCw className="h-3 w-3" />重试
          </button>
        </div>
      )}
      {data && (
        <div className="mt-2 space-y-2">
          {data.blocking_reasons.map(reason => (
            <div key={reason} className="flex items-start gap-1.5 rounded border border-red-500/25 bg-red-500/5 px-2 py-1.5 text-[11px] text-red-300">
              <CircleX className="mt-0.5 h-3 w-3 shrink-0" /> <span>{reason}</span>
            </div>
          ))}
          {data.warnings.map(warning => (
            <div key={warning} className="flex items-start gap-1.5 rounded border border-amber-400/25 bg-amber-400/5 px-2 py-1.5 text-[11px] text-amber-200">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" /> <span>{warning}</span>
            </div>
          ))}
          {!data.blocking_reasons.length && !data.warnings.length && (
            <p className="text-[11px] text-secondary">日线预热和分钟线关键时间点完整。</p>
          )}
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 border-t border-border/60 pt-2">
            <CoverageLine label="日线请求交易日" value={String(data.coverage.daily.requested_day_count ?? '—')} />
            <CoverageLine label="日线预热" value={String(data.coverage.daily.warmup_days ?? '—') + ' 天'} />
            <CoverageLine label="分钟交易日" value={String(data.coverage.minute.trading_days ?? '—')} />
            <CoverageLine label="分钟行数" value={String(data.coverage.minute.row_count ?? '—')} />
          </div>
        </div>
      )}
    </section>
  )
}

const executionStatus = (status: string) => {
  if (status === 'filled') return { label: '已成交', cls: 'text-emerald-400' }
  if (status === 'rejected') return { label: '已拒单', cls: 'text-red-400' }
  if (status === 'queued') return { label: '已排队', cls: 'text-amber-300' }
  return { label: '已触发', cls: 'text-secondary' }
}

export function Etf159915ExecutionTrace({ result }: { result: StrategyBacktestResult }) {
  const rows = buildEtf159915ExecutionRows(result.signal_diagnostics ?? [], result.fills ?? [])
  if (!rows.length) {
    return <div className="p-8 text-center text-sm text-muted">本次回测没有产生信号。</div>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1120px] text-sm text-foreground">
        <thead className="bg-elevated">
          <tr className="text-left text-secondary">
            <th className="px-4 py-2.5 font-medium">信号时间</th>
            <th className="px-4 py-2.5 font-medium">下一分钟成交</th>
            <th className="px-4 py-2.5 font-medium">方向 / 规则</th>
            <th className="px-4 py-2.5 text-right font-medium">成交价</th>
            <th className="px-4 py-2.5 text-right font-medium">数量</th>
            <th className="px-4 py-2.5 text-right font-medium">费用 / 滑点</th>
            <th className="px-4 py-2.5 font-medium">状态</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const status = executionStatus(row.status)
            return (
              <tr key={row.signalId} className="border-t border-border hover:bg-elevated/50">
                <td className="whitespace-nowrap px-4 py-2 font-mono text-xs text-secondary">
                  <div>{row.signalTime}</div>
                  <div className="mt-0.5 text-[10px] text-muted">信号后 {row.dueAt ?? '—'}</div>
                </td>
                <td className="whitespace-nowrap px-4 py-2 font-mono text-xs">
                  {row.fillDatetime ?? '—'}
                </td>
                <td className="px-4 py-2">
                  <div className={row.direction === 'LONG' ? 'text-accent' : 'text-secondary'}>
                    {row.direction === 'LONG' ? '买入' : '卖出'}
                  </div>
                  <div className="mt-0.5 font-mono text-[10px] text-muted">{row.reason}</div>
                </td>
                <td className="px-4 py-2 text-right font-mono">{row.fillPrice == null ? '—' : fmtPrice(row.fillPrice)}</td>
                <td className="px-4 py-2 text-right font-mono">{row.shares == null ? '—' : row.shares.toLocaleString('zh-CN')}</td>
                <td className="px-4 py-2 text-right font-mono text-xs">
                  {row.fillPrice == null ? '—' : String((row.commission ?? 0).toFixed(2)) + ' / ' + String((row.slippage ?? 0).toFixed(2))}
                </td>
                <td className="whitespace-nowrap px-4 py-2 text-xs">
                  <div className={status.cls}>{status.label}</div>
                  {row.rejectionReason && <div className="mt-0.5 text-[10px] text-red-300">{row.rejectionReason}</div>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
