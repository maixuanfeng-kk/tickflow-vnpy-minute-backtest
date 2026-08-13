## 交付记录

- 任务/目标：将本地每日账本 CSV 导出和开盘突破条件 1 的日 K 昨日最高价口径合入主干。
- 分支：`feature/20260805-daily-ledger-export-breakout-high`。
- 改动：vn.py 日线上下文从本地 Tushare/XBX 日 K 读取前一交易日 `high`，前复权模式按信号投影缩放，缺失日 K 高点时明确失败；回测结果页在“每日账本”页签增加 UTF-8 BOM CSV 下载。
- 未迁移：本地工作树中的股票池筛选、数据导入器、Tushare 其他适配和无关配置改动均未暂存或提交。
- 验证：`pytest tests/vnpy_backtest -q` 为 36 passed；前端 TypeScript 检查和 Vite 生产构建通过；`git diff --check` 无输出。
- 冲突记录：无。主干回测页的查询键、成交方式和 `engine` 参数保持不变，仅加入导出按钮。
- 回滚：使用针对性 `git revert <commit>`；若回滚合并提交，使用 `git revert -m 1 <merge-commit>`。
