import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, Database, FolderInput, Loader2, X } from 'lucide-react'
import { Modal } from '@/components/Modal'
import { toast } from '@/components/Toast'
import { api, type StockPoolReadiness } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

interface Props {
  readiness?: StockPoolReadiness
  strategyId: string
  month: string
  onClose: () => void
}

export function StockPoolDataImportDialog({ readiness, strategyId, month, onClose }: Props) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const handledTerminalJob = useRef<string | null>(null)
  const [sourceDir, setSourceDir] = useState('')
  const status = useQuery({
    queryKey: QK.dailyProImportStatus,
    queryFn: api.dailyProImportStatus,
    refetchInterval: query => {
      const job = query.state.data?.job
      return job && (job.status === 'pending' || job.status === 'running') ? 1_000 : 30_000
    },
  })
  const startImport = useMutation({
    mutationFn: () => api.dailyProImportStart(sourceDir.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QK.dailyProImportStatus })
      toast('专业日 K 导入任务已启动', 'success')
    },
    onError: error => toast(error instanceof Error ? error.message : '专业日 K 导入启动失败', 'error'),
  })
  const job = status.data?.job
  const manifest = status.data?.manifest
  const running = job?.status === 'pending' || job?.status === 'running' || startImport.isPending

  useEffect(() => {
    if (!job?.id || handledTerminalJob.current === job.id) return
    if (job.status !== 'succeeded' && job.status !== 'failed') return
    handledTerminalJob.current = job.id
    queryClient.invalidateQueries({ queryKey: QK.stockPoolReadiness(strategyId, month) })
    queryClient.invalidateQueries({ queryKey: QK.dataStatus })
  }, [job?.id, job?.status, month, queryClient, strategyId])

  const submit = () => {
    if (!sourceDir.trim()) {
      toast('请输入专业日 K CSV 目录', 'error')
      inputRef.current?.focus()
      return
    }
    startImport.mutate()
  }

  return (
    <Modal
      onClose={onClose}
      labelledBy="stock-pool-data-import-title"
      initialFocusRef={inputRef}
      closeOnBackdrop
      panelClassName="w-[94vw] max-w-2xl rounded-card border border-border bg-surface shadow-xl"
    >
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 id="stock-pool-data-import-title" className="text-sm font-medium">导入股票池所需数据</h2>
          <p className="mt-1 text-xs text-secondary">导入完成后会自动重新检查 {month} 的数据状态。</p>
        </div>
        <button onClick={onClose} aria-label="关闭" className="rounded-btn p-1.5 text-secondary hover:bg-elevated hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="space-y-4 p-5">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium"><AlertTriangle className="h-4 w-4 text-warning" />当前缺失项</div>
          <ul className="mt-2 space-y-1 pl-6 text-xs text-warning">
            {(readiness?.missing ?? ['尚未取得数据就绪结果']).map(item => <li key={item} className="list-disc">{item}</li>)}
          </ul>
        </div>

        <div className="border-t border-border pt-4">
          <div className="flex items-start gap-2">
            <Database className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
            <div>
              <h3 className="text-xs font-medium">专业日 K CSV</h3>
              <p className="mt-1 text-xs leading-relaxed text-secondary">目录需包含按年份组织的文件，例如 <code>2026/000001_SZ.csv</code>。服务端将扫描并写入股票池专用数据集。</p>
            </div>
          </div>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input
              ref={inputRef}
              value={sourceDir}
              onChange={event => setSourceDir(event.target.value)}
              disabled={running}
              placeholder="例如：F:\\quant\\tushare\\A股日K"
              className="min-w-0 flex-1 rounded-btn border border-border bg-base px-3 py-2 text-sm outline-none placeholder:text-muted focus:border-accent disabled:opacity-50"
            />
            <button onClick={submit} disabled={running} className="inline-flex items-center justify-center gap-2 rounded-btn bg-accent px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">
              {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderInput className="h-4 w-4" />}
              {running ? '导入中' : '扫描并导入'}
            </button>
          </div>
        </div>

        {job && (job.status === 'pending' || job.status === 'running') ? (
          <div className="rounded-btn border border-accent/20 bg-accent/[0.04] p-3">
            <div className="flex items-center justify-between gap-3 text-xs"><span className="truncate text-secondary">{job.log.at(-1)?.msg ?? '准备导入'}</span><span className="font-mono text-accent">{job.progress}%</span></div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-border"><div className="h-full rounded-full bg-accent transition-all" style={{ width: `${job.progress}%` }} /></div>
          </div>
        ) : null}

        {job?.status === 'failed' ? (
          <div className="flex items-start gap-2 rounded-btn border border-danger/30 bg-danger/[0.04] p-3 text-xs text-danger"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span>{job.error ?? '导入失败，原有数据未被覆盖。'}</span></div>
        ) : null}

        {job?.status === 'succeeded' ? (
          <div className="flex items-center gap-2 text-xs text-bull"><CheckCircle2 className="h-4 w-4" />导入完成，数据就绪状态已刷新。</div>
        ) : null}

        {manifest ? (
          <div className="grid gap-3 border-t border-border pt-4 text-xs sm:grid-cols-3">
            <div><div className="text-muted">有效行数</div><div className="mt-1 font-mono text-foreground">{manifest.rows_valid.toLocaleString()}</div></div>
            <div><div className="text-muted">覆盖日期</div><div className="mt-1 font-mono text-foreground">{manifest.earliest_date ?? '—'} 至 {manifest.latest_date ?? '—'}</div></div>
            <div><div className="text-muted">成功文件</div><div className="mt-1 font-mono text-foreground">{manifest.files_imported} / {manifest.files_discovered}</div></div>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
