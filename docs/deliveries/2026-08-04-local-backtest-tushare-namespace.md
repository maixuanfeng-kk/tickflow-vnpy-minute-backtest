## 交付记录

- 任务/目标：将本地 vn.py 组合分钟回测、月度股票池和 Tushare 本地命名空间导入能力合入 `main`。
- 负责人例外：任务负责人明确要求直接在 `main` 集成；仍执行逐文件暂存、回归测试和非强推送。
- 数据优先级：新导入器通过 `python -m app.scripts.import_tushare_local_data --source-root <目录>` 写入 `kline_daily_tushare`、`kline_minute_tushare`、`financial_tushare`、`adj_factor_tushare`。vn.py 和股票池优先读取这些本地命名空间；主干原有标准导入接口和 `kline_daily`、`kline_minute`、`financials`、`adj_factor` 保留为兼容能力。
- 保留的主干行为：优化回测、Walk-forward、标准数据导入、导出、自选和非回测页面 API 均未删除。
- 已执行验证：Tushare CLI `--help`；后端 `77 passed`；前端 TypeScript 检查和 Vite 生产构建通过。
- 冲突处理：共享回测 API 保留主干额外的请求字段和任务端点，但映射到本地组合引擎的单一成交量上限；单日分钟读取优先分区文件；旧 `adj_factor` 继续可被复权投影器读取。
- 回滚：按本次提交逆序执行 `git revert <commit>`；不改写共享历史。
