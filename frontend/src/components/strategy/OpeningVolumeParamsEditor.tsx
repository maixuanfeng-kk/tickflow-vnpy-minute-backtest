import { Clock3, GitBranch, LockKeyhole, ShieldCheck } from 'lucide-react'

import type { StrategyParamDef } from '@/lib/api'

interface OpeningVolumeParamsEditorProps {
  definitions: StrategyParamDef[]
  values: Record<string, any>
  onChange: (id: string, value: any) => void
}

interface BranchDefinition {
  id: 'a' | 'b' | 'c'
  title: string
  subtitle: string
  enabledParam: string
  fieldIds: string[]
}

const COMMON_FIELD_IDS = [
  'scan_start_time',
  'scan_end_time',
  'stop_loss_pct',
  'ma_exit_period',
]

const BRANCHES: BranchDefinition[] = [
  {
    id: 'a',
    title: 'A 分支',
    subtitle: '前日 K 线突破',
    enabledParam: 'enable_branch_a',
    fieldIds: ['branch_a_previous_candle', 'branch_a_volume_multiple'],
  },
  {
    id: 'b',
    title: 'B 分支',
    subtitle: '当日温和上涨',
    enabledParam: 'enable_branch_b',
    fieldIds: [
      'branch_b_volume_multiple',
      'branch_b_today_return_min',
      'branch_b_today_return_max',
      'branch_b_previous_return_max',
    ],
  },
  {
    id: 'c',
    title: 'C 分支',
    subtitle: '前日 K 线延续',
    enabledParam: 'enable_branch_c',
    fieldIds: [
      'branch_c_previous_candle',
      'branch_c_volume_multiple',
      'branch_c_previous_return_max',
    ],
  },
]

const INPUT_CLASS = `w-full rounded-lg border border-border bg-base px-2.5 py-1.5 text-xs text-foreground
  outline-none transition-colors focus:border-accent/60 disabled:cursor-not-allowed`

const isEnabled = (value: any, fallback: any) => {
  const current = value ?? fallback
  return current === true || current === 'true' || current === 'True'
}

const clamp = (value: number, min?: number, max?: number) => {
  let next = value
  if (min != null) next = Math.max(next, min)
  if (max != null) next = Math.min(next, max)
  return next
}

function ParameterField({
  definition,
  value,
  disabled = false,
  onChange,
}: {
  definition: StrategyParamDef
  value: any
  disabled?: boolean
  onChange: (value: any) => void
}) {
  const label = definition.label.replace(/^[ABC]\s+/, '')

  if (definition.type === 'time') {
    return (
      <label className="block min-w-0">
        <span className="mb-1 block text-[11px] text-secondary">{label}</span>
        <input
          type="time"
          value={String(value ?? definition.default)}
          disabled={disabled}
          onChange={event => onChange(event.target.value)}
          className={INPUT_CLASS}
        />
      </label>
    )
  }

  if (definition.type === 'select') {
    return (
      <label className="block min-w-0">
        <span className="mb-1 block text-[11px] text-secondary">{label}</span>
        <select
          value={value ?? definition.default}
          disabled={disabled}
          onChange={event => onChange(event.target.value)}
          className={INPUT_CLASS}
        >
          {(definition.options ?? []).map(option => (
            <option key={option} value={option}>{option}</option>
          ))}
        </select>
      </label>
    )
  }

  const isPercent = definition.type === 'percent'
  const currentValue = Number(value ?? definition.default)
  const displayValue = isPercent ? currentValue * 100 : currentValue
  const min = definition.min == null ? undefined : definition.min * (isPercent ? 100 : 1)
  const max = definition.max == null ? undefined : definition.max * (isPercent ? 100 : 1)
  const step = (definition.step ?? (definition.type === 'int' ? 1 : 0.01)) * (isPercent ? 100 : 1)

  return (
    <label className="block min-w-0">
      <span className="mb-1 block text-[11px] text-secondary">{label}</span>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={Number.isFinite(displayValue) ? displayValue : ''}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onChange={event => {
            if (event.target.value === '') {
              onChange(definition.default)
              return
            }
            const next = clamp(Number(event.target.value), min, max)
            if (isPercent) {
              onChange(next / 100)
            } else if (definition.type === 'int') {
              onChange(Math.round(next))
            } else {
              onChange(next)
            }
          }}
          className={INPUT_CLASS}
        />
        {isPercent && <span className="shrink-0 text-xs text-muted">%</span>}
      </div>
    </label>
  )
}

function BranchToggle({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  return (
    <button
      type="button"
      aria-label={`${label}开关`}
      aria-pressed={checked}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
        checked ? 'bg-accent' : 'bg-elevated'
      }`}
    >
      <span className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
        checked ? 'translate-x-[18px]' : 'translate-x-0.5'
      }`} />
    </button>
  )
}

export function OpeningVolumeParamsEditor({
  definitions,
  values,
  onChange,
}: OpeningVolumeParamsEditorProps) {
  const definitionsById = new Map(definitions.map(definition => [definition.id, definition]))
  const commonDefinitions = COMMON_FIELD_IDS
    .map(id => definitionsById.get(id))
    .filter((definition): definition is StrategyParamDef => definition != null)

  return (
    <div className="space-y-4">
      <section>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-foreground">
          <Clock3 className="h-3.5 w-3.5 text-accent" />
          公共参数
        </div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {commonDefinitions.map(definition => (
            <ParameterField
              key={definition.id}
              definition={definition}
              value={values[definition.id]}
              onChange={value => onChange(definition.id, value)}
            />
          ))}
        </div>
      </section>

      <section>
        <div className="mb-2 flex items-center gap-2 text-xs font-medium text-foreground">
          <GitBranch className="h-3.5 w-3.5 text-accent" />
          入场分支
        </div>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
          {BRANCHES.map(branch => {
            const enabledDefinition = definitionsById.get(branch.enabledParam)
            if (!enabledDefinition) return null
            const enabled = isEnabled(values[branch.enabledParam], enabledDefinition.default)
            const fieldDefinitions = branch.fieldIds
              .map(id => definitionsById.get(id))
              .filter((definition): definition is StrategyParamDef => definition != null)

            return (
              <div key={branch.id} className="rounded-lg border border-border bg-surface/60 p-3">
                <div className="flex items-start justify-between gap-3 border-b border-border/60 pb-2.5">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-foreground">{branch.title}</div>
                    <div className="mt-0.5 text-[10px] text-muted">{branch.subtitle}</div>
                  </div>
                  <BranchToggle
                    label={branch.title}
                    checked={enabled}
                    onChange={checked => onChange(branch.enabledParam, checked)}
                  />
                </div>

                <div className={`mt-3 grid grid-cols-1 gap-3 transition-opacity sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2 ${
                  enabled ? '' : 'pointer-events-none opacity-35'
                }`}>
                  {fieldDefinitions.map(definition => (
                    <ParameterField
                      key={definition.id}
                      definition={definition}
                      value={values[definition.id]}
                      disabled={!enabled}
                      onChange={value => onChange(definition.id, value)}
                    />
                  ))}
                  {branch.id === 'a' && (
                    <div className="flex min-h-9 items-center gap-2 rounded-lg border border-emerald-500/25 bg-emerald-500/5 px-2.5 py-2 text-[11px] text-emerald-300 sm:col-span-2 lg:col-span-1 xl:col-span-2">
                      <LockKeyhole className="h-3.5 w-3.5 shrink-0" />
                      必须突破昨日最高价
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      <div className="flex items-center gap-2 text-[10px] text-muted">
        <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
        启用分支后，分支内全部条件同时满足才会产生入场信号
      </div>
    </div>
  )
}
