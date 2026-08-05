## 交付记录

- 任务/目标：将本地 vn.py 股票池分钟回测的引擎、四个开盘突破策略、SSE API 和完整结果页同步到私有仓库 `main`。
- 分支：`feature/20260805-local-vnpy-backtest-parity`。
- 提交身份：`XiaLe296 <XiaLe296@users.noreply.github.com>`。
- 保留行为：股票池筛选、Tushare 导入、选股/实时策略共享矩阵、分钟触发工具和其他非回测页面保持可用。
- 新行为：回测工作台只提供 vn.py 股票池组合分钟回测；公开 API 收敛为策略目录、流式执行和取消任务。
- 数据与部署：Windows 源码执行 `uv sync --extra vnpy-backtest`；行情与复权数据由管理员导入 Tushare 本地命名空间，不提交 CSV、Parquet、`.env` 或 Token。
- 删除/替换：任务负责人明确确认删除旧 vn.py 单标的、因子回测、参数优化和 Walk-forward 入口。旧能力与新的股票池订单账本、策略注册表和结果页口径不再并行维护。
- 冲突处理：共享 API、前端类型和存储逐段合并；本地回测行为优先，主干非回测能力保留。复权通用兼容层保留，仅收紧 vn.py 当前调用接口。
- 验证：`uv sync --extra vnpy-backtest --extra dev` 成功；复权与 vn.py 专项测试 41 项通过，共享矩阵/分钟触发/选股回归 84 项通过，本地分钟 CSV 导入 2 项通过，vn.py API 5 项通过；前端 TypeScript 检查和 Vite 生产构建通过。
- 全量回归：删除旧 API 专属测试后为 441 项通过、1 项跳过、1 项失败。唯一失败是主干既有的 `test_legacy_migration_status_and_action`，原因是 `app.api.watchlist` 调用了 `app.services.watchlist` 中不存在的 `legacy_migration_status`；本次未修改股票池/自选模块，故不越界修复。
- 静态检查：本次新写的 `app/api/backtest.py` 与 `tests/vnpy_backtest/test_api.py` 通过 Ruff；迁入策略文件原有中文全角标点等 Ruff 告警未批量改写，避免改变策略代码。
- 合并与同步：merge commit、GitHub Actions 结果和远端同步计数由最终交付说明记录。
- 已知限制：Docker 默认镜像与 GitHub Release 桌面安装包不默认包含 vn.py；完整回测交付面向源码部署。
- 回滚：整体回滚执行 `git revert -m 1 <merge-commit>`，不改写共享历史。
