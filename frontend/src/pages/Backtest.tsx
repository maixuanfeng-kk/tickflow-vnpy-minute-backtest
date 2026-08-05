import { PageHeader } from '@/components/PageHeader'
import { StrategyBacktest } from './backtest/StrategyBacktest'

/** The backtest workspace is intentionally limited to the vn.py minute engine. */
export function Backtest() {
  return (
    <div className="min-h-full bg-base flex flex-col">
      <PageHeader
        title="vn.py 分钟回测"
        subtitle="使用 Tushare 分钟 K 数据运行组合策略回测，成交和资金计算保持原始价格口径"
        className="shrink-0 bg-base/95"
      />
      <main className="flex-1 min-h-0 px-3 pb-3 pt-3 lg:px-4 lg:pb-4">
        <StrategyBacktest />
      </main>
    </div>
  )
}
