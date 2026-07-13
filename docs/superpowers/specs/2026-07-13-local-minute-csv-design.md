# Local Minute CSV Data Design

## Goal

让 TickFlow Stock Panel 使用用户提供的本地分钟数据目录 `D:\quant\data`，为股票详情页和现有分钟 K API 提供 1 分钟 OHLCV 数据，不依赖 TickFlow 远程分钟权限，也不修改原始 CSV 文件。

## Current context

项目当前已有两类分钟数据路径：

1. 项目自身 `data/kline_minute` 下的 Parquet 查询。
2. TickFlow 远程 API 的 `period="1m"` 拉取与同步。

外部本地数据没有接入现有查询链路。用户目录包含按股票拆分的 CSV，例如 `D:\quant\data\2026\sh600000.csv`，首行为说明，第二行为中文表头，数据为 1 分钟 K 线。

## Chosen approach

新增一个运行时只读的本地 CSV 适配器，而不是把外部数据复制到内部 Parquet 或建立启动时全量索引。

优点：

- 原始数据保持不变，文件更新后下次查询即可看到。
- 不需要 API Key、Pro+ 能力或远程网络。
- 只解析请求股票对应的文件，避免启动时扫描约 5531 个 CSV。
- 适配器可独立测试，未来可以替换为 Parquet/数据库实现。

## Architecture and data flow

```text
GET /api/kline/minute
        |
        v
project Parquet repository (existing local cache)
        |
        | empty or incomplete
        v
LocalMinuteCsvSource(root=MINUTE_CSV_DIR)
        |
        v
D:\quant\data\{year}\{exchange}{code}.csv
        |
        v
canonical minute rows -> API response -> minute chart
```

项目内部 Parquet 仍然优先，以保持已有同步数据和查询行为兼容；外部 CSV 作为只读补充。外部 CSV 查询失败、文件不存在或日期没有数据时返回空结果，不回退远程 TickFlow，确保“数据使用本地”的行为明确。

## Local source contract

新增 `LocalMinuteCsvSource`，职责仅限于路径解析、CSV 读取、字段规范化和按日期筛选。

### Configuration

- 新增设置 `minute_csv_dir`。
- 默认值为 `D:\quant\data`。
- 支持环境变量 `MINUTE_CSV_DIR` 覆盖，便于测试、部署和未来迁移。
- 目录不存在时适配器返回空结果并记录 debug 日志，不阻止后端启动。

### Symbol mapping

系统符号和文件符号使用以下映射：

| 系统符号 | 文件名 |
| --- | --- |
| `600000.SH` | `sh600000.csv` |
| `000001.SZ` | `sz000001.csv` |
| `920000.BJ` | `bj920000.csv` |

只接受 `SH`、`SZ`、`BJ` 三个交易所后缀和数字代码；无效符号返回空结果，不抛出用户可见的 500 错误。

### CSV normalization

读取每只股票对应的 CSV 时：

- 跳过首行数据说明。
- 以 UTF-8（兼容 BOM/容错读取）读取中文表头。
- 映射 `股票代码`、`k线结束时间`、`开盘价`、`收盘价`、`最高价`、`最低价`、`成交量`、`成交额`。
- 将时间规范化为无时区 `datetime`，数值列规范化为浮点数。
- 按 `datetime` 升序返回，去除无效时间或无法转换的数值行。
- 输出至少包含 `symbol`, `datetime`, `open`, `high`, `low`, `close`, `volume`, `amount`，与现有分钟接口和图表兼容。

### Query behavior

适配器提供：

- `get(symbol, trade_date)`：读取单只股票并过滤自然日。
- `latest_date(symbol)`：从对应 CSV 的有效时间列取得最新交易日。

API 行为调整为：

1. 若请求明确日期，先查内部 Parquet；没有数据时查本地 CSV。
2. 若未提供日期，先查内部 Parquet 的最新日期；没有日期时使用本地 CSV 最新日期。
3. 外部 CSV 返回时使用 `source: "local_csv"`。
4. 没有数据时返回空 `rows` 和 `source: "none"`。
5. 对本地数据不调用 `fetch_minute_single`，避免意外访问远程 TickFlow。

指数分钟接口继续保留现有 TickFlow 行为，因为用户提供的目录是股票文件，且本次范围只覆盖股票分钟 K。

## Background sync behavior

外部 CSV 是现成的历史数据，不需要通过 `/sync_minute` 或 `/extend_minute_history` 复制到项目数据目录。分钟同步偏好和历史扩展接口在本地 CSV 模式下应跳过/提示“本地 CSV 已作为数据源”，不触发远程分钟拉取。

内部 Parquet 同步逻辑保持不变，以免影响已有用户。远程 TickFlow 模式仍可保留为显式兼容路径，但本地 CSV 模式不得隐式回退远程。

## Error handling

- 配置目录不存在：返回空数据，日志记录 debug。
- 股票代码格式无效：返回空数据。
- 文件编码或 CSV 结构异常：记录 warning，返回空数据。
- 日期列不存在或全部无效：记录 warning，返回空数据。
- 单只股票文件异常不得影响其他请求或后端启动。

## Testing

新增后端测试，使用临时目录构造最小 CSV，不读取真实 `D:\quant\data`：

1. `SH/SZ/BJ` 系统符号正确映射到文件名。
2. 跳过说明行并正确解析中文字段和数值。
3. 指定日期只返回该日的分钟行并按时间排序。
4. 未指定日期时返回最新交易日。
5. 无效符号、缺失文件、缺失日期返回空结果。
6. `/api/kline/minute` 使用本地 CSV 时返回 `source: "local_csv"`，且不调用远程抓取函数。
7. 现有 Parquet 有数据时仍优先返回 Parquet 结果。

验证包括新增测试、现有后端测试、ruff 检查，以及前端 TypeScript 构建（若本次接口类型或文案有变更）。

## Scope boundaries

本次不做：

- 全量 CSV 转 Parquet。
- 修改或删除 `D:\quant\data` 原始文件。
- 为指数、ETF 或回测新增分钟数据源。
- 改造 TickFlow SDK 或远程 API。
- 新增复杂的文件监控、后台索引或跨进程缓存。
