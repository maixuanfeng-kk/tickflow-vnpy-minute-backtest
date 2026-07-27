# 交付记录：vn.py 股票池分钟回测迁移

- 任务/目标：以本地 CSV 驱动的股票池组合分钟回测替换旧单标的 vn.py 与旧 `minute_portfolio` 回测入口。
- 分支：`feature/20260727-vnpy-stock-pool-migration`
- 提交：待验证与提交后补充。
- 改动：新增本地 GBK CSV 导入、标准分钟 Parquet 仓储读取、组合撮合引擎、开盘突破股票池策略及条件 1 变体；更新 SSE 回测 API、前端策略配置和结果展示。
- 保留的旧行为：日频/矩阵回测保持不变；`minute_trigger.py`、其日频引擎调用和开盘放量选股扫描保持可用。后者已迁移到 `app.strategy.opening_volume_scan`，不再依赖旧组合回测模块。
- 删除/替换：删除仅支持单标的双均线的 vn.py 模块，以及旧 `minute_portfolio` 回测服务和专属 API/测试。任务负责人于 2026-07-27 明确确认删除；双轨保留会造成两个分钟撮合与结果口径并存，无法安全共存。
- 受影响调用方与迁移：旧 vn.py 页面/API 统一改用策略注册表；旧开盘放量扫描 API 改用独立策略层扫描模块，接口行为保留。
- 已执行验证：`python -m pytest tests/test_local_minute_import.py tests/vnpy_backtest tests/test_opening_volume_strategy.py -q`，28 passed；`corepack pnpm build` 通过。测试使用现有本机 vn.py 虚拟环境，临时目录固定在迁移克隆内。
- 未执行验证：本机没有全局 `uv` 命令，因此未执行 `uv sync`；`pyproject.toml` 与目标 `main` 的依赖声明无实际差异，目标既有 `uv.lock` 保持不变。成员按文档执行 `uv sync --extra vnpy-backtest` 即可创建干净运行环境。
- 已知限制：原始 CSV、Parquet、`.env` 和回测产物不纳入版本库；成员需自行配置 `LOCAL_MINUTE_CSV_DIR` 并导入数据。
- 回滚：若合并后的主干出现问题，执行 `git revert -m 1 <迁移合并提交>`，恢复旧主干实现；数据目录不受代码回滚影响。
- 冲突记录：`backtest.py`、数据/仓储/配置、前端回测任务与页面均与旧分钟回测分支重叠。最终保留目标主干的日频/矩阵能力，以新的股票池分钟框架替换旧单标的与旧 `minute_portfolio` 入口。
