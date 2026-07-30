/**
 * 解析用户粘贴的 A 股代码，仅保留带交易所后缀的标准代码并按输入顺序去重。
 */
export function parseWatchlistSymbols(text: string): string[] {
  const symbols = new Set<string>()
  const codes = text.toUpperCase().match(/\b\d{6}\.(?:SH|SZ|BJ)\b/g) ?? []

  for (const code of codes) symbols.add(code)
  return [...symbols]
}
