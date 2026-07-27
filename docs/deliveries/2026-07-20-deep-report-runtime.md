# 深度研报功能交付记录（2026-07-20）

## 交付目标

以私有库分钟回测版本为唯一基线，语义移植 TickFlow 深度研报功能；保留分钟回测、矩阵引擎、优化器、Walk-forward、ETF 与既有配置/API。前端以深度研报替换旧个股分析入口，旧后端接口保留兼容。

## 变更边界

本次新增深度研报任务 API、受控 FinSight 运行器、任务状态持久化、附件下载、前端任务管理、Windows 安装校验脚本、PyInstaller runner 数据文件、文档和测试。FinSight 修改在独立私有 GPL-3.0 runtime 仓库维护，并通过 `vendor/finsight` submodule 固定提交。

未纳入本次交付的内容包括本机 `.env`、密钥、虚拟环境、数据、生成报告、缓存、日志、截图、本地 vn.py 下载目录，以及与研报无关的策略指南和 Vite 生成文件差异。

## 安全与兼容性

移除了本机 FinSight 绝对路径硬编码；股票代码和名称经过格式与路径边界校验；默认最大并发为一；状态 JSON 原子写入；取消、删除和附件下载限定在受控任务目录；健康接口不暴露本机绝对路径；FinSight 不再把 API Key 持久化到报告目录 `config.json`。

## 验证记录

本次本机验证已完成：

- 深度研报后端单元测试：`12 passed, 1 skipped`（目录符号链接测试因当前 Windows 主机不可创建链接而跳过）。
- 现有 vn.py/矩阵回测回归：`9 passed`。
- 前端严格 TypeScript 检查与 Vite 生产构建：通过。
- PowerShell 安装、校验、真实链路冒烟脚本：Windows PowerShell 语法检查通过。
- FinSight 非模型测试：`10 passed, 2 skipped`；固定脱敏 Markdown 的 Pandoc → DOCX → Word → PDF 版式冒烟：通过。

真实模型冒烟测试会产生费用，必须显式传入 `-ConfirmModelCost`；跨机器 PDF 版式仍须按 `docs/deep-report-windows.md` 在第二台 Windows + Word 设备复验，不要求 PDF 文件整体哈希一致。
