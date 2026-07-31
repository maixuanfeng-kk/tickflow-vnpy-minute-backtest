import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Database, Loader2, Play, RefreshCw } from 'lucide-react'
import { toast } from '@/components/Toast'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

function statusLabel(status: string | undefined) {
  if (status === 'running' || status === 'pending') return '导入中'
  if (status === 'succeeded') return '已完成'
  if (status === 'failed') return '导入失败'
  return '未导入'
}

export function FinancialImportPanel() {
  const queryClient = useQueryClient()
  const [sourceDir, setSourceDir] = useState('')
  const status = useQuery({
    queryKey: QK.financialImportStatus,
    queryFn: api.financialImportStatus,
    refetchInterval: query => {
      const job = query.state.data?.job
      return job && (job.status === 'pending' || job.status === 'running') ? 1_000 : 30_000
    },
  })
  const startImport = useMutation({
    mutationFn: () => api.financialImportStart(sourceDir.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QK.financialImportStatus })
      queryClient.invalidateQueries({ queryKey: QK.pipelineJobs })
      toast('财务数据导入任务已启动', 'success')
    },
    onError: error => toast(error instanceof Error ? error.message : '财务数据导入启动失败', 'error'),
  })

  const job = status.data?.job
  const manifest = status.data?.manifest
  const dataset = status.data?.dataset
  useEffect(() => {
    if (job?.status === 'succeeded' || job?.status === 'failed') {
      queryClient.invalidateQueries({ queryKey: QK.dataStatus })
      queryClient.invalidateQueries({ queryKey: QK.pipelineJobs })
    }
  }, [job?.status, queryClient])
  const running = job?.status === 'pending' || job?.status === 'running' || startImport.isPending
  const submit = () => {
    if (!sourceDir.trim()) {
      toast('请输入已解压的财务数据目录', 'error')
      return
    }
    startImport.mutate()
  }

  return (
    <section className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-2">
          <Database className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
          <div>
            <h2 className="text-sm font-medium text-foreground">财务数据导入</h2>
            <p className="mt-1 text-xs leading-relaxed text-secondary">
              输入已解压的 XBX 财报 CSV 目录。导入后保留原始字段，供后续股票池策略按公告日读取。
            </p>
          </div>
        </div>
        <span className={`shrink-0 text-xs ${job?.status === 'failed' ? 'text-danger' : 'text-secondary'}`}>
          {statusLabel(job?.status ?? manifest?.status)}
        </span>
      </div>

      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        <input
          value={sourceDir}
          onChange={event => setSourceDir(event.target.value)}
          placeholder="例如：C:\\data\\stock-fin-data-xbx"
          disabled={running}
          className="min-w-0 flex-1 rounded-btn border border-border bg-base px-3 py-2 text-sm text-foreground outline-none placeholder:text-muted focus:border-accent disabled:opacity-50"
        />
        <button
          onClick={submit}
          disabled={running}
          className="inline-flex items-center justify-center gap-2 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {running ? '导入中' : '扫描并导入'}
        </button>
      </div>

      {job && (job.status === 'pending' || job.status === 'running') && (
        <div className="mt-4 rounded-btn border border-accent/20 bg-accent/[0.04] p-3">
          <div className="flex items-center justify-between text-xs">
            <span className="text-secondary">{job.log.at(-1)?.msg ?? '准备导入'}</span>
            <span className="font-mono text-accent">{job.progress}%</span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border">
            <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${job.progress}%` }} />
          </div>
        </div>
      )}

      {job?.status === 'failed' && (
        <div className="mt-4 flex items-start gap-2 rounded-btn border border-danger/30 bg-danger/[0.04] p-3 text-xs text-danger">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{job.error ?? '导入任务失败，上一份成功数据仍然保留。'}</span>
        </div>
      )}

      {(manifest || dataset) && (
        <div className="mt-4 grid gap-3 border-t border-border pt-4 sm:grid-cols-2 lg:grid-cols-4">
          <div><div className="text-[11px] text-muted">成功文件</div><div className="mt-1 font-mono text-sm">{manifest?.files_imported ?? 0} / {manifest?.files_discovered ?? 0}</div></div>
          <div><div className="text-[11px] text-muted">财报行数</div><div className="mt-1 font-mono text-sm">{dataset?.rows ?? manifest?.income_rows ?? 0}</div></div>
          <div><div className="text-[11px] text-muted">最新报告期</div><div className="mt-1 font-mono text-sm">{dataset?.latest_report_date ?? '—'}</div></div>
          <div><div className="text-[11px] text-muted">最新公告日</div><div className="mt-1 font-mono text-sm">{dataset?.latest_publish_date ?? '—'}</div></div>
        </div>
      )}

      {manifest?.failed_files?.length ? (
        <div className="mt-3 rounded-btn border border-warning/30 bg-warning/[0.04] p-3 text-xs text-warning">
          <div className="flex items-center gap-1.5">
            <RefreshCw className="h-3.5 w-3.5 shrink-0" />
            有 {manifest.failed_files.length} 个文件未能导入
          </div>
          <ul className="mt-2 space-y-1 break-words pl-5 text-secondary">
            {manifest.failed_files.slice(0, 3).map(item => (
              <li key={`${item.file}-${item.reason}`}>
                {item.file}: {item.reason}
              </li>
            ))}
          </ul>
          {manifest.failed_files.length > 3 ? (
            <p className="mt-2 text-muted">仅显示前 3 条失败记录。</p>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
