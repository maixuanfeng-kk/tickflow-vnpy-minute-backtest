import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Database, FolderInput, RefreshCw, Save, Workflow } from 'lucide-react'
import { EmptyState } from '@/components/EmptyState'
import { PageHeader } from '@/components/PageHeader'
import { toast } from '@/components/Toast'
import { api, type StockPoolResult } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { StockPoolDataImportDialog } from '@/components/stock-pools/StockPoolDataImportDialog'

function statusText(status?: string) {
  return status === 'ready' ? '数据已就绪' : '数据尚未就绪'
}

export function StockPools() {
  const queryClient = useQueryClient()
  const [month, setMonth] = useState('2026-05')
  const [strategyId, setStrategyId] = useState('monthly_growth_trend')
  const [result, setResult] = useState<StockPoolResult | null>(null)
  const [params, setParams] = useState<Record<string, number>>({})
  const [selectedMonthlyPoolKey, setSelectedMonthlyPoolKey] = useState<string | null>(null)
  const [showDataImport, setShowDataImport] = useState(false)
  const strategies = useQuery({
    queryKey: QK.stockPoolStrategies,
    queryFn: api.stockPoolStrategies,
  })
  const readiness = useQuery({
    queryKey: QK.stockPoolReadiness(strategyId, month),
    queryFn: () => api.stockPoolReadiness(strategyId, month),
  })
  const dailyProImportStatus = useQuery({
    queryKey: QK.dailyProImportStatus,
    queryFn: api.dailyProImportStatus,
    refetchInterval: query => {
      const job = query.state.data?.job
      return job && (job.status === 'pending' || job.status === 'running') ? 1_000 : 30_000
    },
  })
  const runs = useQuery({
    queryKey: QK.stockPoolRuns(strategyId),
    queryFn: () => api.stockPoolRuns(strategyId),
  })
  const monthlyPools = useQuery({
    queryKey: QK.watchlistPools,
    queryFn: api.watchlistPools,
  })
  const selectedMonthlyPool = useQuery({
    queryKey: QK.watchlist(selectedMonthlyPoolKey ?? ''),
    queryFn: () => api.watchlistList(selectedMonthlyPoolKey ?? undefined),
    enabled: selectedMonthlyPoolKey !== null,
  })
  const activeStrategy = strategies.data?.strategies.find(item => item.id === strategyId)
  useEffect(() => {
    if (!activeStrategy) return
    // 策略切换时按后端描述恢复默认参数，避免沿用不兼容的旧参数。
    setParams(Object.fromEntries(activeStrategy.parameters.map(parameter => [parameter.key, parameter.default])))
  }, [activeStrategy])

  const preview = useMutation({
    mutationFn: () => api.stockPoolPreview(strategyId, month, params),
    onSuccess: setResult,
    onError: () => toast('股票池预览失败，请检查数据状态。', 'error'),
  })
  const save = useMutation({
    mutationFn: () => api.stockPoolSave(strategyId, month, params),
    onSuccess: (saved) => {
      setResult(saved)
      queryClient.invalidateQueries({ queryKey: QK.stockPoolRuns(strategyId) })
      queryClient.invalidateQueries({ queryKey: QK.watchlistPools })
      toast('筛选结果已生成独立股票池，手工模板未修改。', 'success')
    },
    onError: () => toast('保存失败：请先确认数据已就绪并完成预览。', 'error'),
  })

  const activeReadiness = result?.readiness ?? readiness.data
  const ready = activeReadiness?.status === 'ready'
  const importJob = dailyProImportStatus.data?.job
  const importRunning = importJob?.status === 'pending' || importJob?.status === 'running'

  useEffect(() => {
    if (!importJob?.id || (importJob.status !== 'succeeded' && importJob.status !== 'failed')) return
    queryClient.invalidateQueries({ queryKey: QK.stockPoolReadiness(strategyId, month) })
    queryClient.invalidateQueries({ queryKey: QK.dataStatus })
  }, [importJob?.id, importJob?.status, month, queryClient, strategyId])
  const copyCodeString = async () => {
    if (!result?.code_string) return
    try {
      await navigator.clipboard.writeText(result.code_string)
      toast('股票代码串已复制，可粘贴到开盘突破回测。', 'success')
    } catch {
      toast('复制失败，请手动复制代码串。', 'error')
    }
  }

  return (
    <div className="min-h-full">
      <PageHeader
        title="股票池构建"
        subtitle="按月固定，以月初前一交易日可获得的信息构建研究股票池"
      />

      <div className="mx-auto max-w-7xl space-y-4 p-5">
        <section className="rounded-card border border-border bg-surface p-4">
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_10rem_auto] md:items-end">
            <label className="block">
              <span className="mb-1.5 block text-xs text-secondary">筛选策略</span>
              <select
                value={strategyId}
                onChange={event => {
                  setStrategyId(event.target.value)
                  setResult(null)
                }}
                className="w-full rounded-btn border border-border bg-base px-3 py-2 text-sm outline-none focus:border-accent"
              >
                {(strategies.data?.strategies ?? []).map(strategy => (
                  <option key={strategy.id} value={strategy.id}>{strategy.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1.5 block text-xs text-secondary">股票池月份</span>
              <input
                type="month"
                value={month}
                onChange={event => {
                  setMonth(event.target.value)
                  setResult(null)
                }}
                className="w-full rounded-btn border border-border bg-base px-3 py-2 text-sm outline-none focus:border-accent"
              />
            </label>
            <button
              onClick={() => preview.mutate()}
              disabled={!ready || preview.isPending}
              className="inline-flex items-center justify-center gap-2 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              <RefreshCw className={`h-4 w-4 ${preview.isPending ? 'animate-spin' : ''}`} />
              预览构建
            </button>
          </div>
          <p className="mt-3 text-xs text-secondary">
            候选范围：完整本地市场数据；保存后将在自选中生成 {month} 的独立条件选股池，不会修改任何手工模板。
          </p>
          {activeStrategy && (
            <>
              <p className="mt-3 text-xs leading-relaxed text-secondary">{activeStrategy.description}</p>
              <div className="mt-4 grid gap-3 border-t border-border pt-4 sm:grid-cols-2 lg:grid-cols-3">
                {activeStrategy.parameters.map(parameter => (
                  <label key={parameter.key} className="block">
                    <span className="mb-1.5 block text-xs text-secondary">
                      {parameter.label}{parameter.unit ? ` (${parameter.unit})` : ''}
                    </span>
                    <input
                      type="number"
                      value={params[parameter.key] ?? parameter.default}
                      min={parameter.min}
                      max={parameter.max}
                      step={parameter.step ?? 1}
                      onChange={event => setParams(current => ({ ...current, [parameter.key]: Number(event.target.value) }))}
                      className="w-full rounded-btn border border-border bg-base px-3 py-2 text-sm outline-none focus:border-accent"
                    />
                  </label>
                ))}
              </div>
            </>
          )}
        </section>

        <section className={`rounded-card border p-4 ${ready ? 'border-bull/30 bg-bull/[0.04]' : 'border-warning/30 bg-warning/[0.04]'}`}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Database className={`h-4 w-4 ${ready ? 'text-bull' : 'text-warning'}`} />
              <h2 className="text-sm font-medium">数据就绪检查：{statusText(activeReadiness?.status)}</h2>
              {activeReadiness?.as_of_date && <span className="text-xs text-secondary">时点 {activeReadiness.as_of_date}</span>}
            </div>
            {!ready ? (
              <button onClick={() => setShowDataImport(true)} className="inline-flex items-center gap-2 rounded-btn border border-warning/40 bg-warning/10 px-3 py-2 text-xs font-medium text-warning hover:bg-warning/15">
                <FolderInput className="h-4 w-4" />导入所需数据
              </button>
            ) : null}
          </div>
          {importRunning ? (
            <button
              type="button"
              onClick={() => setShowDataImport(true)}
              className="mt-3 block w-full rounded-btn border border-accent/20 bg-accent/[0.04] p-3 text-left hover:border-accent/40"
              aria-label="查看专业日K导入进度"
            >
              <div className="flex items-center justify-between gap-3 text-xs">
                <span className="truncate text-secondary">专业日K导入进度：{importJob.log.at(-1)?.msg ?? '准备导入'}</span>
                <span className="shrink-0 font-mono text-accent">{importJob.progress}%</span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border">
                <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${importJob.progress}%` }} />
              </div>
            </button>
          ) : null}
          {activeReadiness?.warnings.map(warning => <p key={warning} className="mt-2 text-xs text-warning">{warning}</p>)}
          {!ready && (
            <div className="mt-2 text-xs leading-relaxed text-secondary">
              <p>正式构建需要目标月前一交易日的专业日 K，以及截至该日已公告的财报。</p>
              {(activeReadiness?.missing ?? []).map(item => <p key={item} className="mt-1 text-warning">缺失：{item}</p>)}
            </div>
          )}
        </section>

        {showDataImport ? (
          <StockPoolDataImportDialog
            readiness={activeReadiness}
            strategyId={strategyId}
            month={month}
            onClose={() => setShowDataImport(false)}
          />
        ) : null}

        {result?.status === 'ready' ? (
          <>
            <section className="rounded-card border border-border bg-surface p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-medium">预览结果</h2>
                  <p className="mt-1 text-xs text-secondary">{result.month} 股票池，共 {result.members.length} 只，候选范围全市场，时点 {result.as_of_date}</p>
                </div>
                <button
                  onClick={() => save.mutate()}
                  disabled={save.isPending}
                  className="inline-flex items-center gap-2 rounded-btn border border-accent/40 bg-accent/10 px-3 py-2 text-sm text-accent disabled:opacity-50"
                >
                  <Save className="h-4 w-4" />
                  保存并生成股票池
                </button>
              </div>
              <div className="mt-4 rounded-btn border border-border bg-base p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs text-secondary">回测粘贴代码</span>
                  <button onClick={copyCodeString} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
                    <Copy className="h-3.5 w-3.5" />复制
                  </button>
                </div>
                <p className="mt-2 break-all font-mono text-xs text-foreground">{result.code_string || '无符合条件的股票'}</p>
              </div>
            </section>

            <section className="overflow-hidden rounded-card border border-border bg-surface">
              <div className="border-b border-border px-4 py-3 text-sm font-medium">成员与命中证据</div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-xs">
                  <thead className="bg-elevated/60 text-secondary">
                    <tr>
                      <th className="px-4 py-2.5 font-medium">代码</th>
                      <th className="px-4 py-2.5 font-medium">名称</th>
                      <th className="px-4 py-2.5 font-medium">总市值</th>
                      <th className="px-4 py-2.5 font-medium">条件 1</th>
                      <th className="px-4 py-2.5 font-medium">条件 2</th>
                      <th className="px-4 py-2.5 font-medium">营收同比</th>
                      <th className="px-4 py-2.5 font-medium">净利润</th>
                      <th className="px-4 py-2.5 font-medium">收盘 / MA60</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.members.map(member => (
                      <tr key={member.symbol} className="border-t border-border/70">
                        <td className="px-4 py-2.5 font-mono">{member.symbol}</td>
                        <td className="px-4 py-2.5">{member.stock_name ?? '—'}</td>
                        <td className="px-4 py-2.5">{member.market_cap == null ? '—' : `${(member.market_cap / 100_000_000).toFixed(1)} 亿`}</td>
                        <td className="px-4 py-2.5">{member.condition_1 ? '命中' : '—'}</td>
                        <td className="px-4 py-2.5">{member.condition_2 ? '命中' : '—'}</td>
                        <td className="px-4 py-2.5">{member.revenue_yoy == null ? '—' : `${(member.revenue_yoy * 100).toFixed(1)}%`}</td>
                        <td className="px-4 py-2.5">{member.net_profit == null ? '—' : member.net_profit.toLocaleString()}</td>
                        <td className="px-4 py-2.5">{member.close?.toFixed(2) ?? '—'} / {member.ma60?.toFixed(2) ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : (
          <EmptyState icon={Workflow} title="等待数据就绪后预览" hint="本页不会在数据缺失时生成或保存正式股票池；补齐数据后可直接使用同一策略与界面构建。" />
        )}

        <section className="rounded-card border border-border bg-surface p-4">
          <h2 className="text-sm font-medium">已手动保存的历史结果</h2>
          {(runs.data?.runs ?? []).length === 0 ? (
            <p className="mt-2 text-xs text-secondary">暂无已保存结果。</p>
          ) : (
            <div className="mt-3 space-y-2">
              {runs.data?.runs.map(run => (
                <div key={run.run_id} className="flex items-center justify-between rounded-btn bg-elevated/60 px-3 py-2 text-xs">
                  <span>{run.month} · 全市场筛选 · {run.member_count} 只 · 时点 {run.as_of_date ?? '—'}</span>
                  <span className="font-mono text-muted">{run.run_id}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-card border border-border bg-surface p-4">
          <h2 className="text-sm font-medium">月度自选池</h2>
          {(monthlyPools.data?.pools ?? []).length === 0 ? (
            <p className="mt-2 text-xs text-secondary">保存筛选结果后，这里会显示对应月份的独立股票池。</p>
          ) : (
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {(monthlyPools.data?.pools ?? []).map(pool => (
                <button
                  key={pool.pool_key}
                  type="button"
                  onClick={() => setSelectedMonthlyPoolKey(pool.pool_key)}
                  className="rounded-btn border border-border/70 bg-elevated/50 px-3 py-2 text-left text-xs transition-colors hover:border-accent/50"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-foreground">{pool.label ?? pool.month ?? pool.pool_key}</span>
                    <span className="font-mono text-muted">{pool.member_count} 只</span>
                  </div>
                  <div className="mt-1 text-muted">{pool.source === 'stock_pool' ? '策略生成池' : '手工月度池'} · {pool.updated_at ?? '—'}</div>
                </button>
              ))}
            </div>
          )}
          {selectedMonthlyPoolKey && (
            <div className="mt-3 rounded-btn border border-accent/30 bg-accent/[0.04] p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-foreground">{selectedMonthlyPoolKey}</span>
                <span className="text-muted">{selectedMonthlyPool.data?.symbols.length ?? 0} 只</span>
              </div>
              <p className="mt-2 break-all font-mono text-[11px] text-secondary">
                {(selectedMonthlyPool.data?.symbols ?? []).map(member => member.symbol).join(',') || '加载中或该池为空'}
              </p>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
