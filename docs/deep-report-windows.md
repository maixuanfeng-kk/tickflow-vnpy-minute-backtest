# Windows 深度研报安装与验收

TickFlow 的“个股分析”页面已替换为 FinSight 深度研报工作台。TickFlow 负责任务管理和文件下载，FinSight 作为独立 GPL-3.0 runtime 负责资料采集、分析、DOCX 排版和调用 Microsoft Word 转换 PDF。两个仓库通过 Git submodule 固定版本，禁止把本机 FinSight 目录整包复制进 TickFlow。

## 支持范围与锁定环境

该功能只支持 Windows x64，并要求本机具有合法 Microsoft Word。参考验收环境如下：

| 组件 | 锁定版本 |
| :--- | :--- |
| FinSight 基线 | `8fc5bce28ac201c713219a47d30367c6a84342f4` |
| Python | `3.10.20`（FinSight 独立虚拟环境） |
| Pandoc | `3.10` |
| python-docx | `1.2.0` |
| docx2pdf | `0.1.8` |
| Microsoft Word | `16.0.20131.20154` |
| `report_template.docx` SHA-256 | `94382CA50E050CD0526610F4CAB8D4717ADB3BD6C190F8B17166EA6C14A674FE` |

`vendor/finsight/finsight-windows-lock.json` 还锁定中文大纲、字体资源和必需字体。PDF 元数据会包含生成时间等差异，因此跨机器验收比较页面尺寸、页数、分页、字体、换行、目录和页眉页脚，不比较整个 PDF 文件哈希。

## 首次安装

在 PowerShell 中执行：

```powershell
git clone --recurse-submodules https://github.com/maixuanfeng-kk/tickflow-vnpy-minute-backtest.git
Set-Location .\tickflow-vnpy-minute-backtest
.\scripts\setup-finsight.ps1
```

如果已经克隆过主仓库，先补齐 submodule：

```powershell
git submodule update --init --recursive
.\scripts\setup-finsight.ps1
```

脚本会创建 `vendor/finsight/.venv`、安装锁定依赖、从示例创建 FinSight `.env`，随后运行完整环境检查。它不会安装 Microsoft Word，也不会代填任何模型或搜索密钥。

在 TickFlow 根目录 `.env` 保持以下配置：

```ini
FINSIGHT_ROOT=./vendor/finsight
FINSIGHT_PYTHON=./vendor/finsight/.venv/Scripts/python.exe
FINSIGHT_MAX_CONCURRENT=1
```

然后只在 `vendor/finsight/.env` 中填写本机的模型、Embedding、VLM 和搜索服务配置。不要把 `.env`、API Key、运行输出或生成报告提交到 Git。

## 启动前检查

每次升级 Word、Python、Pandoc、字体、模板或 FinSight submodule 后执行：

```powershell
.\scripts\verify-finsight.ps1
```

检查内容包括 FinSight 锁定清单、Python 与依赖版本、Pandoc、Word、模板/大纲/字体文件 SHA-256、Windows 字体和必要的 `.env` 键。脚本只报告键是否配置，不显示密钥值。

启动 TickFlow 后，深度研报页面会调用 `/api/stock-analysis/deep-reports/health`。健康信息仅返回就绪状态和可操作警告，不返回本机完整绝对路径。默认同一时间只允许运行一份研报。

## API 与任务生命周期

深度研报 API 提供健康检查、任务目录、运行创建、列表/详情、取消、删除和附件下载。创建任务后由后台独立进程执行；状态文件采用原子替换写入，服务重启后仍可读取历史任务。只能删除已结束的任务，取消请求会终止对应的受控子进程，附件下载只允许访问该任务目录内已登记的文件。

旧个股分析后端接口暂时保留供外部调用迁移，但前端入口与悬浮入口均指向深度研报。

## 真实链路冒烟测试

下列脚本会调用实际模型，可能产生费用；仅在确认后执行：

```powershell
.\scripts\smoke-deep-report.ps1 -Symbol "000001.SZ" -Name "平安银行" -ConfirmModelCost
```

脚本先验证健康状态和任务目录，再创建研报、轮询至结束，并把可用的 Markdown、DOCX、PDF 和日志下载到 `%TEMP%\TickFlowDeepReportSmoke\<run-id>`。

## 跨机器 PDF 版式验收

在至少两台 Windows + Word 电脑上使用同一脱敏报告夹具、同一 FinSight submodule 提交和同一锁定环境生成 DOCX/PDF。逐项核对页面尺寸、页数、分页点、正文字体与字号、中文换行、目录页码、页眉页脚和图表位置。允许创建时间、作者等 PDF 元数据变化；如果版式不同，先运行 `verify-finsight.ps1`，不要直接替换模板或手工修改生成文件。

## 许可证与禁止提交内容

FinSight runtime 保留上游 GPL-3.0 许可证和来源说明；TickFlow 主仓库只通过 submodule 引用其固定提交。不得提交 `.env`、API Key、`.venv/`、`data/`、`outputs/`、`node_modules/`、缓存、日志、生成报告、备份文件、本地截图或 `report_template_yulan.docx`。
