import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Ban,
  Bell,
  CheckSquare,
  Clock3,
  Download,
  FileText,
  History,
  Loader2,
  RefreshCw,
  Sparkles,
  Trash2,
} from 'lucide-react'

import { EmptyState } from '@/components/EmptyState'
import { LastStockChip } from '@/components/LastStockChip'
import { PriceAlertDialog } from '@/components/stock-analysis/PriceAlertDialog'
import { PageHeader } from '@/components/PageHeader'
import { StockFinancialSearch } from '@/components/financials/StockFinancialSearch'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { toast } from '@/components/Toast'
import {
  api,
  type DeepReportArtifactKind,
  type DeepReportRun,
  type DeepReportTask,
} from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { useLastStock } from '@/lib/useLastStock'

function splitTasks(value: string): string[] {
  return value
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
}

function fmtTime(value?: string | null): string {
  if (!value) return '--'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function statusLabel(run: DeepReportRun | null) {
  if (!run) return '未选择'
  if (run.status === 'queued') return '排队中'
  if (run.status === 'running') return '生成中'
  if (run.status === 'succeeded') return run.pdf_status === 'ready' ? '已完成' : 'Word 已完成'
  if (run.status === 'cancelled') return '已终止'
  return '失败'
}

function statusClass(run: DeepReportRun | null) {
  if (!run) return 'border-border bg-elevated text-secondary'
  if (run.status === 'queued') return 'border-amber-400/30 bg-amber-400/10 text-amber-300'
  if (run.status === 'running') return 'border-sky-400/30 bg-sky-400/10 text-sky-300'
  if (run.status === 'succeeded') return 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'
  if (run.status === 'cancelled') return 'border-zinc-400/30 bg-zinc-400/10 text-zinc-300'
  return 'border-rose-400/30 bg-rose-400/10 text-rose-300'
}

export function StockAnalysis() {
  const qc = useQueryClient()
  const { last: lastStock, remember: rememberStock } = useLastStock('stock-analysis')
  const [symbol, setSymbol] = useState(lastStock?.symbol ?? '')
  const [name, setName] = useState(lastStock?.name ?? '')
  const [showPriceAlerts, setShowPriceAlerts] = useState(false)
  const [collectTaskIds, setCollectTaskIds] = useState<string[]>([])
  const [analysisTaskIds, setAnalysisTaskIds] = useState<string[]>([])
  const [seededTasks, setSeededTasks] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)

  const healthQuery = useQuery({
    queryKey: QK.stockDeepReportHealth,
    queryFn: api.stockDeepReportHealth,
    staleTime: 60_000,
  })

  const taskCatalogQuery = useQuery({
    queryKey: QK.stockDeepReportTaskCatalog,
    queryFn: api.stockDeepReportTaskCatalog,
    staleTime: Infinity,
  })

  const runsQuery = useQuery({
    queryKey: QK.stockDeepReportRuns(),
    queryFn: () => api.stockDeepReportRuns(),
    refetchInterval: (query) => {
      const runs = query.state.data?.runs ?? []
      return runs.some(run => run.status === 'queued' || run.status === 'running') ? 3000 : 15000
    },
    refetchIntervalInBackground: true,
  })

  const detailQuery = useQuery({
    queryKey: selectedRunId ? QK.stockDeepReportRun(selectedRunId) : ['stock-deep-report-run', 'empty'],
    queryFn: () => api.stockDeepReportDetail(selectedRunId!),
    enabled: !!selectedRunId,
    refetchInterval: (query) => {
      const run = query.state.data?.run
      return run && (run.status === 'queued' || run.status === 'running') ? 3000 : false
    },
    refetchIntervalInBackground: true,
  })

  const allRuns = runsQuery.data?.runs ?? []
  const runs = useMemo(() => {
    const current = runsQuery.data?.runs ?? []
    if (!symbol) return current
    const normalized = symbol.trim().toUpperCase()
    return current.filter(run => run.symbol.toUpperCase() === normalized)
  }, [runsQuery.data?.runs, symbol])

  const createMut = useMutation({
    mutationFn: () => api.stockDeepReportCreate({
      symbol,
      name,
      collect_task_ids: collectTaskIds,
      analysis_task_ids: analysisTaskIds,
    }),
    onSuccess: ({ run }) => {
      toast('深度研报任务已创建', 'success')
      qc.setQueryData<{ runs: DeepReportRun[] }>(QK.stockDeepReportRuns(), current => ({
        runs: [run, ...(current?.runs ?? []).filter(item => item.id !== run.id)],
      }))
      qc.setQueryData(QK.stockDeepReportRun(run.id), { run })
      setSelectedRunId(run.id)
      qc.invalidateQueries({ queryKey: QK.stockDeepReportRuns() })
    },
  })

  const deleteMut = useMutation({
    mutationFn: (runId: string) => api.stockDeepReportDelete(runId),
    onSuccess: (_res, runId) => {
      qc.setQueryData<{ runs: DeepReportRun[] }>(QK.stockDeepReportRuns(), current => ({
        runs: (current?.runs ?? []).filter(item => item.id !== runId),
      }))
      qc.removeQueries({ queryKey: QK.stockDeepReportRun(runId) })
      if (selectedRunId === runId) {
        setSelectedRunId(null)
      }
      toast('已删除研报记录', 'success')
      qc.invalidateQueries({ queryKey: QK.stockDeepReportRuns() })
    },
  })

  const cancelMut = useMutation({
    mutationFn: (runId: string) => api.stockDeepReportCancel(runId),
    onSuccess: ({ run }) => {
      toast('已终止研报生成', 'success')
      qc.setQueryData<{ runs: DeepReportRun[] }>(QK.stockDeepReportRuns(), current => ({
        runs: (current?.runs ?? []).map(item => item.id === run.id ? run : item),
      }))
      qc.setQueryData(QK.stockDeepReportRun(run.id), { run })
      setSelectedRunId(run.id)
      qc.invalidateQueries({ queryKey: QK.stockDeepReportRuns() })
      qc.invalidateQueries({ queryKey: QK.stockDeepReportRun(run.id) })
    },
  })

  useEffect(() => {
    if (!taskCatalogQuery.data) return
    const availableCollect = taskCatalogQuery.data.collect_tasks.map(task => task.id)
    const availableAnalysis = taskCatalogQuery.data.analysis_tasks.map(task => task.id)
    setCollectTaskIds(current => seededTasks
      ? current.filter(id => availableCollect.includes(id))
      : availableCollect)
    setAnalysisTaskIds(current => seededTasks
      ? current.filter(id => availableAnalysis.includes(id))
      : availableAnalysis)
    if (!seededTasks) setSeededTasks(true)
  }, [seededTasks, taskCatalogQuery.data])

  useEffect(() => {
    if (runs.length === 0) {
      setSelectedRunId(null)
      return
    }
    if (!selectedRunId || !runs.some(run => run.id === selectedRunId)) {
      setSelectedRunId(runs[0].id)
    }
  }, [runs, selectedRunId])

  const selectedRun = detailQuery.data?.run ?? null

  const onSelect = (nextSymbol: string, nextName: string) => {
    setSymbol(nextSymbol)
    setName(nextName)
    setShowPriceAlerts(false)
    rememberStock(nextSymbol, nextName)
  }

  const startRun = () => {
    if (!symbol) {
      toast('请先选择个股', 'error')
      return
    }
    if (!healthQuery.data?.ready) {
      toast('FinSight 环境尚未就绪，请先运行安装脚本并重新检查', 'error')
      return
    }
    if (activeRun) {
      toast(`已有研报正在生成：${activeRun.name || activeRun.symbol}`, 'error')
      return
    }
    if (collectTaskIds.length === 0 || analysisTaskIds.length === 0) {
      toast('数据收集和数据分析任务都至少选择一项', 'error')
      return
    }
    createMut.mutate()
  }

  const cancelRun = (runId?: string | null) => {
    if (!runId) return
    if (!window.confirm('确认终止这次研报生成吗？已产生的中间结果可能不完整。')) return
    cancelMut.mutate(runId)
  }

  const resetTemplates = () => {
    if (!taskCatalogQuery.data) return
    setCollectTaskIds(taskCatalogQuery.data.collect_tasks.map(task => task.id))
    setAnalysisTaskIds(taskCatalogQuery.data.analysis_tasks.map(task => task.id))
    toast('已恢复默认任务模板', 'success')
  }

  const downloadArtifact = (kind: DeepReportArtifactKind) => {
    if (!selectedRun) return
    const artifact = selectedRun.artifacts[kind]
    if (!artifact.available) {
      toast(`${artifact.label || kind} 附件尚不可用`, 'error')
      return
    }
    const url = artifact.download_url || api.stockDeepReportDownloadUrl(selectedRun.id, kind)
    window.open(url, '_blank', 'noopener,noreferrer')
  }

  const health = healthQuery.data
  const taskCatalog = taskCatalogQuery.data
  const activeRun = allRuns.find(run => run.status === 'queued' || run.status === 'running') ?? null
  const running = selectedRun?.status === 'queued' || selectedRun?.status === 'running'
  const cancellableRun = running ? selectedRun : activeRun
  const createDisabledReason = activeRun
    ? `已有研报正在生成：${activeRun.name || activeRun.symbol}`
    : !health?.ready
      ? '请先完成 FinSight 环境安装并重新检查'
      : collectTaskIds.length === 0 || analysisTaskIds.length === 0
        ? '数据收集和数据分析任务都至少选择一项'
        : undefined

  return (
    <>
      <PageHeader
        title="深度个股研报"
        titleExtra={<Sparkles className="h-4 w-4 text-amber-300" />}
        subtitle="仅保留 FinSight 深度研究报告链路，生成后提供 Word 与 PDF 附件"
        right={
          <div className="flex items-center gap-2">
            <LastStockChip stock={lastStock} onSelect={onSelect} />
            <span className={`inline-flex items-center rounded-full border px-2 py-1 text-[11px] ${statusClass(selectedRun)}`}>
              {running ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Clock3 className="mr-1 h-3 w-3" />}
              {statusLabel(selectedRun)}
            </span>
          </div>
        }
      />

      <div className="min-h-full bg-[radial-gradient(circle_at_15%_-5%,rgba(245,158,11,0.08),transparent_30%),radial-gradient(circle_at_85%_0%,rgba(59,130,246,0.08),transparent_26%)] px-6 py-6">
        <div className="mx-auto max-w-[1380px] space-y-4">
          <section className="rounded-card border border-border/60 bg-surface/70 p-4">
            <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
              <div className="space-y-3 xl:w-[420px]">
                <div className="text-sm font-medium text-foreground">研究标的</div>
                <StockFinancialSearch onSelect={onSelect} />
                <div className="flex min-h-8 items-center gap-2 text-xs text-secondary">
                  {symbol ? (
                    <>
                      <span className="rounded bg-elevated px-2 py-1 font-mono text-foreground">{symbol}</span>
                      <span>{name || '未命名标的'}</span>
                    </>
                  ) : (
                    <span>选择股票后即可提交深度研报任务</span>
                  )}
                </div>
                {healthQuery.isLoading ? (
                  <div className="flex items-center gap-2 rounded-btn border border-border bg-elevated/40 px-3 py-2 text-xs text-secondary">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    正在检查 FinSight 环境...
                  </div>
                ) : healthQuery.isError ? (
                  <div className="rounded-btn border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-xs text-rose-100">
                    <div className="font-medium">无法读取 FinSight 环境状态</div>
                    <div className="mt-1 text-rose-100/80">请确认 TickFlow 后端已经启动，然后重新检查。</div>
                    <button
                      type="button"
                      onClick={() => healthQuery.refetch()}
                      disabled={healthQuery.isFetching}
                      className="mt-2 inline-flex items-center gap-1 rounded border border-rose-300/30 px-2 py-1 text-[11px] hover:bg-rose-300/10 disabled:opacity-50"
                    >
                      <RefreshCw className={`h-3 w-3 ${healthQuery.isFetching ? 'animate-spin' : ''}`} />
                      重新检查
                    </button>
                  </div>
                ) : health ? (
                  <div className={`rounded-btn border px-3 py-2 text-xs ${health.ready ? 'border-emerald-400/20 bg-emerald-400/5 text-secondary' : 'border-rose-400/30 bg-rose-400/10 text-rose-100'}`}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="font-medium text-foreground">{health.ready ? 'FinSight 环境已就绪' : 'FinSight 环境未就绪'}</div>
                      <button
                        type="button"
                        onClick={() => healthQuery.refetch()}
                        disabled={healthQuery.isFetching}
                        className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-[10px] text-secondary hover:bg-base/50 hover:text-foreground disabled:opacity-50"
                      >
                        <RefreshCw className={`h-3 w-3 ${healthQuery.isFetching ? 'animate-spin' : ''}`} />
                        重新检查
                      </button>
                    </div>
                    {!health.ready && (
                      <div className="mt-1.5 leading-5">
                        <div>请在项目根目录运行 <code className="rounded bg-base/60 px-1 py-0.5">scripts\setup-finsight.ps1</code>，填写本机 <code className="rounded bg-base/60 px-1 py-0.5">.env</code> 后重新检查。</div>
                      </div>
                    )}
                    {health.warnings.map(item => (
                      <div key={item} className="mt-1 opacity-80">{item}</div>
                    ))}
                  </div>
                ) : null}
                {taskCatalogQuery.isError && (
                  <div className="rounded-btn border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-xs text-rose-100">
                    无法加载研报任务库。
                    <button type="button" onClick={() => taskCatalogQuery.refetch()} className="ml-2 underline underline-offset-2">重新加载</button>
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={startRun}
                    disabled={createMut.isPending || !!activeRun || !health?.ready || collectTaskIds.length === 0 || analysisTaskIds.length === 0}
                    title={createDisabledReason}
                    className="inline-flex items-center gap-1.5 rounded-btn bg-amber-500 px-4 py-2 text-xs font-medium text-black transition-colors hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {createMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                    生成深度研报
                  </button>
                  {cancellableRun && (
                    <button
                      onClick={() => cancelRun(cancellableRun.id)}
                      disabled={cancelMut.isPending}
                      className="inline-flex items-center gap-1.5 rounded-btn border border-rose-400/40 bg-rose-400/10 px-4 py-2 text-xs text-rose-200 transition-colors hover:bg-rose-400/20 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {cancelMut.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Ban className="h-3.5 w-3.5" />}
                      终止生成
                    </button>
                  )}
                  <button
                    onClick={resetTemplates}
                    disabled={!taskCatalog}
                    className="inline-flex items-center gap-1.5 rounded-btn border border-border bg-elevated px-4 py-2 text-xs text-secondary transition-colors hover:text-foreground disabled:opacity-50"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    恢复默认模板
                  </button>
                  {symbol && (
                    <button
                      onClick={() => setShowPriceAlerts(true)}
                      className="inline-flex items-center gap-1.5 rounded-btn border border-sky-400/30 bg-sky-400/10 px-4 py-2 text-xs text-sky-200 transition-colors hover:bg-sky-400/20"
                      title="设置价格点位提醒"
                    >
                      <Bell className="h-3.5 w-3.5" />
                      点位提醒
                    </button>
                  )}
                </div>
              </div>

              <div className="grid flex-1 gap-4 lg:grid-cols-2">
                <TaskMultiSelect
                  title="数据收集任务"
                  tasks={taskCatalog?.collect_tasks ?? []}
                  selectedIds={collectTaskIds}
                  onChange={setCollectTaskIds}
                  loading={taskCatalogQuery.isLoading}
                  placeholder="每行一条数据收集任务"
                />
                <TaskMultiSelect
                  title="数据分析任务"
                  tasks={taskCatalog?.analysis_tasks ?? []}
                  selectedIds={analysisTaskIds}
                  onChange={setAnalysisTaskIds}
                  loading={taskCatalogQuery.isLoading}
                  placeholder="每行一条数据分析任务"
                />
              </div>
            </div>
          </section>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <section className="rounded-card border border-border/60 bg-surface/70">
              <div className="flex items-center justify-between border-b border-border/40 px-4 py-3">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-amber-300" />
                  <span className="text-sm font-medium text-foreground">
                    {selectedRun?.report_title || selectedRun?.name || '报告预览'}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {selectedRun?.artifacts.markdown.available && (
                    <ActionButton label="Markdown" onClick={() => downloadArtifact('markdown')} />
                  )}
                  {selectedRun?.artifacts.word.available && (
                    <ActionButton label="Word" onClick={() => downloadArtifact('word')} />
                  )}
                  {selectedRun?.artifacts.pdf.available && (
                    <ActionButton label="PDF" onClick={() => downloadArtifact('pdf')} />
                  )}
                  {selectedRun?.artifacts.log.available && (
                    <ActionButton label="日志" onClick={() => downloadArtifact('log')} />
                  )}
                </div>
              </div>
              <div className="min-h-[620px] max-h-[calc(100vh-18rem)] overflow-y-auto px-5 py-4">
                {detailQuery.isError && selectedRunId ? (
                  <div className="flex min-h-[420px] flex-col items-center justify-center gap-3 text-center">
                    <AlertTriangle className="h-6 w-6 text-rose-300" />
                    <div className="text-sm font-medium text-foreground">研报详情加载失败</div>
                    <div className="text-xs text-secondary">记录可能已被删除，或后端暂时不可用。</div>
                    <button
                      type="button"
                      onClick={() => detailQuery.refetch()}
                      className="inline-flex items-center gap-1.5 rounded-btn border border-border bg-elevated px-3 py-1.5 text-xs text-secondary hover:text-foreground"
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      重新加载
                    </button>
                  </div>
                ) : detailQuery.isLoading && selectedRunId ? (
                  <div className="grid min-h-[560px] place-items-center">
                    <Loader2 className="h-6 w-6 animate-spin text-amber-300" />
                  </div>
                ) : !selectedRun ? (
                  <EmptyState
                    icon={FileText}
                    title="暂无深度研报"
                    hint="左侧选择标的并提交任务后，这里会显示 FinSight 生成的完整 Markdown 报告。"
                  />
                ) : selectedRun.status === 'failed' ? (
                  <ReportError run={selectedRun} />
                ) : selectedRun.status === 'cancelled' ? (
                  <ReportCancelled run={selectedRun} />
                ) : selectedRun.markdown_content ? (
                  <div className="space-y-4">
                    {selectedRun.pdf_status === 'unavailable' && (
                      <div className="rounded-btn border border-amber-400/20 bg-amber-400/10 px-3 py-2 text-xs text-amber-100">
                        <div className="font-medium">PDF 未生成</div>
                        <div className="mt-1">{selectedRun.pdf_error || '当前仅可下载 Word。'}</div>
                      </div>
                    )}
                    <MarkdownRenderer content={selectedRun.markdown_content} />
                  </div>
                ) : selectedRun.status === 'queued' || selectedRun.status === 'running' ? (
                  <div className="flex min-h-[560px] flex-col items-center justify-center gap-3 text-center">
                    <Loader2 className="h-6 w-6 animate-spin text-amber-300" />
                    <div className="text-sm text-foreground">{selectedRun.message || 'FinSight 正在生成研报'}</div>
                    <div className="text-xs text-secondary">
                      {selectedRun.stage} · {Math.min(100, Math.max(0, selectedRun.progress))}%
                    </div>
                  </div>
                ) : (
                  <EmptyState
                    icon={AlertTriangle}
                    title="报告正文不可用"
                    hint="任务已经结束，但没有生成 Markdown 正文；请查看可用附件或运行日志。"
                  />
                )}
              </div>
            </section>

            <aside className="rounded-card border border-border/60 bg-surface/70">
              <div className="flex items-center gap-2 border-b border-border/40 px-4 py-3">
                <History className="h-4 w-4 text-amber-300" />
                <span className="text-sm font-medium text-foreground">研报历史</span>
                <span className="font-mono text-[11px] text-secondary">({runs.length})</span>
              </div>
              <div className="max-h-[calc(100vh-18rem)] overflow-y-auto p-2">
                {runsQuery.isLoading ? (
                  <div className="grid h-32 place-items-center">
                    <Loader2 className="h-4 w-4 animate-spin text-muted" />
                  </div>
                ) : runsQuery.isError ? (
                  <div className="px-3 py-8 text-center text-xs text-rose-200">
                    <div>研报历史加载失败</div>
                    <button type="button" onClick={() => runsQuery.refetch()} className="mt-2 underline underline-offset-2">重新加载</button>
                  </div>
                ) : runs.length === 0 ? (
                  <div className="px-3 py-10 text-center text-xs text-secondary">
                    当前没有深度研报记录
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    {runs.map(run => (
                      <div
                        key={run.id}
                        onClick={() => setSelectedRunId(run.id)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            setSelectedRunId(run.id)
                          }
                        }}
                        className={`w-full cursor-pointer rounded-btn border px-3 py-3 text-left transition-colors ${
                          selectedRunId === run.id
                            ? 'border-amber-400/30 bg-amber-400/10'
                            : 'border-border/40 bg-elevated/30 hover:border-border hover:bg-elevated/60'
                        }`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium text-foreground">{run.name || run.symbol}</div>
                            <div className="mt-1 flex items-center gap-2 text-[11px] text-secondary">
                              <span className="font-mono">{run.symbol}</span>
                              <span>{fmtTime(run.created_at)}</span>
                            </div>
                            <div className="mt-2 truncate text-[11px] text-secondary">
                              {run.message || run.stage}
                            </div>
                          </div>
                          <div className="flex shrink-0 items-center gap-1">
                            <span className={`rounded-full border px-2 py-0.5 text-[10px] ${statusClass(run)}`}>
                              {statusLabel(run)}
                            </span>
                            {(run.status === 'queued' || run.status === 'running') && (
                              <button
                                onClick={(event) => {
                                  event.stopPropagation()
                                  cancelRun(run.id)
                                }}
                                disabled={cancelMut.isPending}
                                className="rounded p-1 text-muted transition-colors hover:bg-elevated hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-40"
                                title="终止生成"
                              >
                                <Ban className="h-3.5 w-3.5" />
                              </button>
                            )}
                            <button
                              onClick={(event) => {
                                event.stopPropagation()
                                if (window.confirm(`确认删除“${run.report_title || run.name || run.symbol}”吗？此操作无法撤销。`)) {
                                  deleteMut.mutate(run.id)
                                }
                              }}
                              disabled={deleteMut.isPending || run.status === 'queued' || run.status === 'running'}
                              className="rounded p-1 text-muted transition-colors hover:bg-elevated hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-40"
                              title="删除"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </aside>
          </div>
        </div>
      </div>
      {showPriceAlerts && symbol && (
        <PriceAlertDialog
          key={symbol}
          symbol={symbol}
          name={name}
          onClose={() => setShowPriceAlerts(false)}
        />
      )}
    </>
  )
}

function TaskMultiSelect({
  title,
  tasks,
  selectedIds,
  onChange,
  loading,
}: {
  title: string
  tasks: DeepReportTask[]
  selectedIds: string[]
  onChange: (ids: string[]) => void
  loading?: boolean
  placeholder?: string
}) {
  const selected = new Set(selectedIds)
  const allSelected = tasks.length > 0 && tasks.every(task => selected.has(task.id))

  const toggleTask = (taskId: string) => {
    onChange(selected.has(taskId)
      ? selectedIds.filter(id => id !== taskId)
      : [...selectedIds, taskId])
  }

  return (
    <div className="rounded-card border border-border/40 bg-elevated/20 p-3">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <CheckSquare className="h-4 w-4 text-amber-300" />
          <span className="text-sm font-medium text-foreground">{title}</span>
          <span className="rounded-full bg-base px-2 py-0.5 text-[10px] text-secondary">
            {selectedIds.length}/{tasks.length}
          </span>
        </div>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => onChange(tasks.map(task => task.id))}
            disabled={tasks.length === 0 || allSelected}
            className="rounded px-1.5 py-1 text-[10px] text-secondary hover:bg-base hover:text-foreground disabled:opacity-40"
          >
            全选
          </button>
          <button
            type="button"
            onClick={() => onChange([])}
            disabled={selectedIds.length === 0}
            className="rounded px-1.5 py-1 text-[10px] text-secondary hover:bg-base hover:text-foreground disabled:opacity-40"
          >
            清空
          </button>
        </div>
      </div>
      {loading ? (
        <div className="grid min-h-[220px] place-items-center text-xs text-secondary">
          <Loader2 className="h-4 w-4 animate-spin text-amber-300" />
        </div>
      ) : tasks.length === 0 ? (
        <div className="grid min-h-[220px] place-items-center rounded-btn border border-dashed border-border/60 px-4 text-center text-xs text-secondary">
          暂无可用任务，请在系统设置中启用或新增任务。
        </div>
      ) : (
        <div className="max-h-[280px] space-y-1.5 overflow-y-auto pr-1">
          {tasks.map(task => {
            const checked = selected.has(task.id)
            return (
              <label
                key={task.id}
                className={`flex cursor-pointer gap-2 rounded-btn border px-2.5 py-2 transition-colors ${
                  checked
                    ? 'border-amber-400/30 bg-amber-400/10'
                    : 'border-border/50 bg-base/50 hover:border-border hover:bg-base'
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleTask(task.id)}
                  className="mt-0.5 h-3.5 w-3.5 accent-amber-400"
                />
                <span className="min-w-0">
                  <span className="block text-xs font-medium text-foreground">{task.title}</span>
                  <span className="mt-0.5 block text-[11px] leading-5 text-secondary">{task.prompt}</span>
                </span>
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function TaskEditor({
  title,
  value,
  onChange,
  placeholder,
}: {
  title: string
  value: string
  onChange: (value: string) => void
  placeholder: string
}) {
  const count = splitTasks(value).length
  return (
    <div className="rounded-card border border-border/40 bg-elevated/20 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">{title}</span>
        <span className="text-[11px] text-secondary">{count} 条</span>
      </div>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="min-h-[220px] w-full resize-y rounded-btn border border-border bg-base px-3 py-2 text-xs leading-6 text-foreground outline-none transition-colors placeholder:text-muted focus:border-amber-400/40"
      />
    </div>
  )
}

function ActionButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-btn border border-border bg-elevated px-2.5 py-1 text-[11px] text-secondary transition-colors hover:text-foreground"
    >
      <Download className="h-3 w-3" />
      {label}
    </button>
  )
}

function ReportError({ run }: { run: DeepReportRun }) {
  return (
    <div className="flex min-h-[420px] flex-col items-center justify-center gap-4 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-full bg-rose-400/10">
        <AlertTriangle className="h-5 w-5 text-rose-300" />
      </div>
      <div className="text-sm font-medium text-foreground">深度研报生成失败</div>
      <div className="max-w-xl text-xs leading-6 text-secondary">
        {run.error || run.message || '请检查 FinSight 环境和日志输出。'}
      </div>
      {run.log_tail && (
        <pre className="max-h-[280px] w-full overflow-auto rounded-card border border-border/40 bg-base px-3 py-3 text-left text-[11px] leading-5 text-secondary">
          {run.log_tail}
        </pre>
      )}
    </div>
  )
}

function ReportCancelled({ run }: { run: DeepReportRun }) {
  return (
    <div className="flex min-h-[420px] flex-col items-center justify-center gap-4 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-full bg-zinc-400/10">
        <Ban className="h-5 w-5 text-zinc-300" />
      </div>
      <div className="text-sm font-medium text-foreground">深度研报生成已终止</div>
      <div className="max-w-xl text-xs leading-6 text-secondary">
        {run.message || 'FinSight runner 已停止，后续不会继续消耗模型 token。'}
      </div>
      {run.log_tail && (
        <pre className="max-h-[280px] w-full overflow-auto rounded-card border border-border/40 bg-base px-3 py-3 text-left text-[11px] leading-5 text-secondary">
          {run.log_tail}
        </pre>
      )}
    </div>
  )
}
