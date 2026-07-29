export function participationRatio(value: string): number {
  const percent = Number(value)
  if (!Number.isFinite(percent)) return 0
  return Math.min(Math.max(percent, 0), 100) / 100
}
