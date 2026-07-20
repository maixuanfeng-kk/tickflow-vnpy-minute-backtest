import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, ListChecks, Pencil, Plus, Save, Trash2, X } from 'lucide-react'

import { toast } from '@/components/Toast'
import { api, type DeepReportTask } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

type TaskDraft = Omit<DeepReportTask, 'id' | 'order'>

const EMPTY_DRAFT: TaskDraft = {
  kind: 'collect',
  title: '',
  prompt: '',
  enabled: true,
}

export function DeepReportTasksSettings() {
  const qc = useQueryClient()
  const catalogQuery = useQuery({
    queryKey: QK.stockDeepReportTaskCatalogAdmin,
    queryFn: api.stockDeepReportTaskCatalogAdmin,
  })
  const [draft, setDraft] = useState<TaskDraft | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)

  const refresh = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: QK.stockDeepReportTaskCatalogAdmin }),
      qc.invalidateQueries({ queryKey: QK.stockDeepReportTaskCatalog }),
    ])
  }

  const saveMut = useMutation({
    mutationFn: async ({ taskId, value }: { taskId: string | null; value: TaskDraft }) => (
      taskId
        ? api.stockDeepReportTaskUpdate(taskId, value)
        : api.stockDeepReportTaskCreate(value)
    ),
    onSuccess: async () => {
      setDraft(null)
      setEditingId(null)
      await refresh()
      toast('任务库已保存', 'success')
    },
  })

  const deleteMut = useMutation({
    mutationFn: api.stockDeepReportTaskDelete,
    onSuccess: async () => {
      await refresh()
      toast('任务已删除', 'success')
    },
  })

  const reorderMut = useMutation({
    mutationFn: ({ kind, ids }: { kind: DeepReportTask['kind']; ids: string[] }) =>
      api.stockDeepReportTaskReorder(kind, ids),
    onSuccess: refresh,
  })

  const tasks = catalogQuery.data
  const moveTask = (kind: DeepReportTask['kind'], taskId: string, direction: -1 | 1) => {
    const source = kind === 'collect' ? tasks?.collect_tasks ?? [] : tasks?.analysis_tasks ?? []
    const index = source.findIndex(task => task.id === taskId)
    const nextIndex = index + direction
    if (index < 0 || nextIndex < 0 || nextIndex >= source.length) return
    const ids = source.map(task => task.id)
    ;[ids[index], ids[nextIndex]] = [ids[nextIndex], ids[index]]
    reorderMut.mutate({ kind, ids })
  }

  const startEdit = (task: DeepReportTask) => {
    setEditingId(task.id)
    setDraft({ kind: task.kind, title: task.title, prompt: task.prompt, enabled: task.enabled })
  }

  const saveDraft = () => {
    if (!draft) return
    if (!draft.title.trim() || !draft.prompt.trim()) {
      toast('请填写任务名称和任务文案', 'error')
      return
    }
    saveMut.mutate({ taskId: editingId, value: draft })
  }

  return (
    <section className="rounded-card border border-border bg-surface p-5 mt-6">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-accent" />
          <div>
            <h3 className="text-sm font-medium text-foreground">深度研报任务库</h3>
            <p className="mt-1 text-[11px] text-muted">用户仅能勾选启用任务；完整任务文案会作为 FinSight 的执行指令。</p>
          </div>
        </div>
        <button
          onClick={() => {
            setEditingId(null)
            setDraft(EMPTY_DRAFT)
          }}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-btn bg-elevated px-3 py-1.5 text-xs text-secondary transition-colors hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" />
          新增任务
        </button>
      </div>

      {draft && (
        <TaskForm
          value={draft}
          saving={saveMut.isPending}
          editing={!!editingId}
          onChange={setDraft}
          onCancel={() => {
            setDraft(null)
            setEditingId(null)
          }}
          onSave={saveDraft}
        />
      )}

      {catalogQuery.isLoading ? (
        <div className="py-8 text-center text-xs text-secondary">正在加载任务库...</div>
      ) : catalogQuery.isError ? (
        <div className="rounded-btn border border-rose-400/30 bg-rose-400/10 px-4 py-5 text-center text-xs text-rose-100">
          <div>深度研报任务库加载失败，请确认后端服务可用。</div>
          <button type="button" onClick={() => catalogQuery.refetch()} className="mt-2 underline underline-offset-2">重新加载</button>
        </div>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          <TaskList
            title="数据收集任务"
            tasks={tasks?.collect_tasks ?? []}
            busy={deleteMut.isPending || reorderMut.isPending}
            onEdit={startEdit}
            onDelete={(task) => {
              if (window.confirm(`确认删除“${task.title}”吗？`)) deleteMut.mutate(task.id)
            }}
            onMove={(task, direction) => moveTask('collect', task.id, direction)}
          />
          <TaskList
            title="数据分析任务"
            tasks={tasks?.analysis_tasks ?? []}
            busy={deleteMut.isPending || reorderMut.isPending}
            onEdit={startEdit}
            onDelete={(task) => {
              if (window.confirm(`确认删除“${task.title}”吗？`)) deleteMut.mutate(task.id)
            }}
            onMove={(task, direction) => moveTask('analysis', task.id, direction)}
          />
        </div>
      )}
    </section>
  )
}

function TaskForm({
  value,
  saving,
  editing,
  onChange,
  onCancel,
  onSave,
}: {
  value: TaskDraft
  saving: boolean
  editing: boolean
  onChange: (value: TaskDraft) => void
  onCancel: () => void
  onSave: () => void
}) {
  return (
    <div className="mb-4 rounded-card border border-accent/25 bg-elevated/30 p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-medium text-foreground">{editing ? '编辑任务' : '新增任务'}</span>
        <button onClick={onCancel} className="rounded p-1 text-muted hover:bg-base hover:text-foreground"><X className="h-4 w-4" /></button>
      </div>
      <div className="grid gap-3 md:grid-cols-[150px_minmax(0,1fr)_auto]">
        <label className="text-xs text-secondary">
          任务类型
          <select
            value={value.kind}
            onChange={(event) => onChange({ ...value, kind: event.target.value as DeepReportTask['kind'] })}
            className="mt-1 h-9 w-full rounded-btn border border-border bg-base px-2 text-xs text-foreground"
          >
            <option value="collect">数据收集</option>
            <option value="analysis">数据分析</option>
          </select>
        </label>
        <label className="text-xs text-secondary">
          任务名称
          <input
            value={value.title}
            maxLength={80}
            onChange={(event) => onChange({ ...value, title: event.target.value })}
            className="mt-1 h-9 w-full rounded-btn border border-border bg-base px-2 text-xs text-foreground outline-none focus:border-accent/60"
            placeholder="例如：三大财务报表"
          />
        </label>
        <label className="flex items-end gap-2 pb-2 text-xs text-secondary">
          <input type="checkbox" checked={value.enabled} onChange={(event) => onChange({ ...value, enabled: event.target.checked })} className="accent-accent" />
          启用
        </label>
      </div>
      <label className="mt-3 block text-xs text-secondary">
        FinSight 任务文案
        <textarea
          value={value.prompt}
          rows={3}
          onChange={(event) => onChange({ ...value, prompt: event.target.value })}
          className="mt-1 w-full resize-y rounded-btn border border-border bg-base px-2 py-2 text-xs leading-5 text-foreground outline-none focus:border-accent/60"
          placeholder="描述需要收集或分析的具体内容"
        />
      </label>
      <div className="mt-3 flex justify-end gap-2">
        <button onClick={onCancel} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:text-foreground">取消</button>
        <button onClick={onSave} disabled={saving} className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-black disabled:opacity-50">
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
      </div>
    </div>
  )
}

function TaskList({
  title,
  tasks,
  busy,
  onEdit,
  onDelete,
  onMove,
}: {
  title: string
  tasks: DeepReportTask[]
  busy: boolean
  onEdit: (task: DeepReportTask) => void
  onDelete: (task: DeepReportTask) => void
  onMove: (task: DeepReportTask, direction: -1 | 1) => void
}) {
  return (
    <div className="rounded-btn border border-border/60 bg-base/30 p-3">
      <div className="mb-2 text-xs font-medium text-foreground">{title} ({tasks.length})</div>
      <div className="space-y-1.5">
        {tasks.map((task, index) => (
          <div key={task.id} className="flex items-start gap-2 rounded-btn border border-border/40 bg-elevated/30 px-2.5 py-2">
            <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${task.enabled ? 'bg-emerald-400' : 'bg-zinc-500'}`} title={task.enabled ? '已启用' : '已停用'} />
            <div className="min-w-0 flex-1">
              <div className="text-xs font-medium text-foreground">{task.title}</div>
              <div className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-muted">{task.prompt}</div>
            </div>
            <div className="flex shrink-0 items-center gap-0.5 text-muted">
              <button disabled={busy || index === 0} onClick={() => onMove(task, -1)} className="rounded p-1 hover:bg-base hover:text-foreground disabled:opacity-30"><ChevronUp className="h-3.5 w-3.5" /></button>
              <button disabled={busy || index === tasks.length - 1} onClick={() => onMove(task, 1)} className="rounded p-1 hover:bg-base hover:text-foreground disabled:opacity-30"><ChevronDown className="h-3.5 w-3.5" /></button>
              <button disabled={busy} onClick={() => onEdit(task)} className="rounded p-1 hover:bg-base hover:text-foreground disabled:opacity-30"><Pencil className="h-3.5 w-3.5" /></button>
              <button disabled={busy} onClick={() => onDelete(task)} className="rounded p-1 hover:bg-base hover:text-rose-300 disabled:opacity-30"><Trash2 className="h-3.5 w-3.5" /></button>
            </div>
          </div>
        ))}
        {tasks.length === 0 && <div className="py-4 text-center text-[11px] text-muted">暂无任务</div>}
      </div>
    </div>
  )
}
