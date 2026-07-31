export function migrateLocalDataCardVisibility(
  saved: Record<string, boolean>,
  version: number,
): Record<string, boolean> {
  if (version >= 2) return saved
  return { ...saved, minute: true, financials: true }
}
