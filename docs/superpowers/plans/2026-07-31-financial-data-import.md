# 财务数据导入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在数据模块增加一个每次手动填写已解压目录、后台执行并展示进度的本地财务数据导入功能，不新增股票池策略。

**Architecture:** 新增数据 API 路由复用 `job_store`、全局重任务锁和 `LocalFinancialCsvImporter`；任务结果写入现有 job JSON，状态接口读取导入清单和已发布 income Parquet。前端新增独立导入面板，使用现有 React Query 和 pipeline job 查询模型，不把目录写入 localStorage。

**Tech Stack:** FastAPI, Pydantic, Polars, Python `ThreadPoolExecutor`, React 18, TypeScript, TanStack Query, Tailwind CSS.

---

### Task 1: 建立导入数据与任务的失败测试

**Files:**
- Create: `backend/tests/test_financial_import.py`
- Test: `backend/app/services/local_financial_import.py`, `backend/app/api/financial_import.py`（Task 2 创建）

- [ ] **Step 1: 写导入器成功、无效目录和保留旧数据测试**

使用 `tmp_path` 创建包含首行说明、表头和一行 `sh600901` 数据的 CSV，断言 `LocalFinancialCsvImporter.run()` 返回成功、manifest 写入且 `financials/income/part.parquet` 包含标准化证券代码、报告期和公告日。另测不存在目录抛出 `ValueError`，并在已有 `income/part.parquet` 时传入只含无效 CSV 的目录，断言旧文件内容未被替换。

- [ ] **Step 2: 写状态摘要测试**

在临时数据目录写入 manifest 和 income Parquet，调用 `_read_financial_import_status(data_dir, active_job=None)`，断言返回 `manifest`、财务库行数、证券数、最新报告期和最新公告日；无文件时返回 `manifest=None`、`dataset=None`。

- [ ] **Step 3: 运行测试确认先失败**

运行：`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_financial_import.py -q`

预期：因状态模块和测试目标接口尚未创建而失败；记录失败原因后进入实现。

### Task 2: 增加后台财务导入 API

**Files:**
- Create: `backend/app/api/financial_import.py`
- Modify: `backend/app/main.py:15,340`（注册路由）
- Test: `backend/tests/test_financial_import.py`

- [ ] **Step 1: 实现目录校验和状态摘要**

在 `financial_import.py` 中定义 `FinancialImportRequest(BaseModel)` 的 `source_dir: str`；实现 `_read_financial_import_status(data_dir, active_job)`，仅返回 manifest 允许字段，并用 Polars 读取 `financials/income/part.parquet` 的 `symbol`、`period_end`、`announce_date` 计算 `rows`、`symbols`、`latest_report_date`、`latest_publish_date`。不返回服务器的绝对源目录。

- [ ] **Step 2: 实现后台任务生命周期**

定义独立单线程执行器和两个函数：同步 `_import_sync(job_id, source_dir, data_dir)` 负责 `job_store.start`、调用 `LocalFinancialCsvImporter(progress=...)`、成功/失败收尾和 `release_run_slot()`；异步 `_run_import(...)` 用 `asyncio.get_running_loop().run_in_executor(_executor, lambda: _import_sync(...))` 承载同步导入。progress 将 `current / total` 映射到 `job_store.progress(job_id, "financial_import", pct, "解析本地财务 CSV (current/total)", stage_pct=pct, skip_log=True)`；成功以 `{ "type": "financial_import", "summary": summary.to_dict() }` 调用 `job_store.succeed`，异常用中文原因调用 `job_store.fail`。

- [ ] **Step 3: 实现 POST/GET 路由**

`POST /api/data/financial-import`：先校验非空目录、目录存在和至少一个 CSV；若 `job_store.active_id()` 存在返回 409；调用 `job_store.create(timeout_s=1800)`，若非新任务返回 409；先调用 `job_store.progress(job_id, "financial_import", 0, "等待开始", stage_pct=0)`，再用 `asyncio.create_task(_run_import(job_id, source_dir, data_dir))` 调度线程池任务并返回 `{job_id, reused:false}`。`GET /api/data/financial-import/status`：读取 `request.app.state.repo.store.data_dir`，将当前 stage 为 `financial_import` 的活动 job 作为 `job` 返回。

- [ ] **Step 4: 注册路由并让数据状态缓存失效**

在 `main.py` 导入 `financial_import` 并 `app.include_router(financial_import.router)`。任务成功后调用 `invalidate_data_cache()` 和 `request.app.state.repo.refresh_cache()`，使数据页和未来股票池读取新财务库。

- [ ] **Step 5: 运行后端测试确认通过**

运行：`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_financial_import.py backend/tests/test_stock_pools.py -q`

预期：导入器、状态摘要和既有股票池测试全部通过。

### Task 3: 增加前端 API 类型、查询键和导入面板

**Files:**
- Create: `frontend/src/components/data/FinancialImportPanel.tsx`
- Modify: `frontend/src/lib/api.ts:Pipeline/Data status sections`
- Modify: `frontend/src/lib/queryKeys.ts:Data / Pipeline section`
- Test: `frontend` TypeScript build

- [ ] **Step 1: 增加 API 类型和方法**

在 `api.ts` 定义 `FinancialImportStatus`、`FinancialImportDataset`、`FinancialImportManifest`；加入 `financialImportStatus()` 和 `financialImportStart(sourceDir)`，分别调用 `GET /api/data/financial-import/status` 与 `POST /api/data/financial-import`。在 `QK` 增加 `financialImportStatus: ['financial-import-status']`。

- [ ] **Step 2: 实现面板状态与提交**

`FinancialImportPanel` 使用 `useState('')` 保存本次输入目录，使用 `useQuery(QK.financialImportStatus)` 轮询活动任务，使用 `useMutation(api.financialImportStart)` 启动导入；活动任务按 1 秒轮询，完成后恢复 30 秒。提交前 trim 并拦截空目录，提交中禁用按钮；错误通过现有 `toast` 显示。

- [ ] **Step 3: 实现运行、成功和失败视图**

运行中显示阶段、百分比进度条和任务日志最后一条；成功显示导入时间、成功文件数、读取行数、income 行数、最新报告期、最新公告日和失败文件数；失败显示错误和失败文件摘要。目录输入不写入浏览器存储，且面板不出现筛选策略控件。

- [ ] **Step 4: 运行前端类型检查**

运行：`pnpm --dir frontend build`

预期：TypeScript 编译和 Vite 构建成功。

### Task 4: 将面板放入数据模块并完成验证

**Files:**
- Modify: `frontend/src/pages/Data.tsx`（导入组件并放在“数据画像”之后、“同步历史”之前）
- Modify: `backend/tests/test_financial_import.py`（补充 API 校验覆盖）

- [ ] **Step 1: 嵌入数据页**

导入 `FinancialImportPanel`，在数据画像区块结束后渲染该组件，使其与日 K/Enriched 概览同页但保持独立区块；数据页现有 pipeline 任务卡和同步按钮行为不改。

- [ ] **Step 2: 补充接口边界测试**

使用 FastAPI 路由函数或 TestClient 覆盖空路径、非目录、无 CSV 和已有任务 409；断言这些请求都不会调用导入器，也不会改变旧 manifest。

- [ ] **Step 3: 运行完整验证**

运行：`$env:PYTHONPATH='backend'; python -m pytest backend/tests/test_financial_import.py backend/tests/test_stock_pools.py -q`、`pnpm --dir frontend build`、`git diff --check`。

预期：后端相关测试通过、前端构建通过、差异检查无输出；若环境缺少依赖，记录确切命令和缺失依赖，不声称测试通过。

- [ ] **Step 4: 提交实现**

只暂存本计划涉及的后端 API/测试、前端 API/查询键/面板/Data 页和本计划文档，运行 `git diff --cached --check` 后提交：`git commit -m "feat(data): add local financial import panel"`。
