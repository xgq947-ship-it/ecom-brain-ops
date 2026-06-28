# TMCS CLI Migration Design

## Goal

在不修改旧项目 `<项目根>/运营自动化工具` 代码的前提下，把两条猫超相关能力迁移到新项目 `Ops-Cli`：

1. `ops tmcs product sync`
2. `ops tmcs bill download`

统一要求：

- 继续复用旧 `SessionHub` 的 scene 和登录态
- 所有新增能力都接入 `Ops-Cli` 现有 CLI / 日志 / context / JSON 输出架构
- 不手填 Cookie，不硬编码 Token
- 第一阶段只保证 `Downloads` 内结果正确，不覆盖其他外部项目文件

## Existing Context

旧项目里已存在并验证过的猫超相关资产：

- `tmall_chaoshi / maochao_item_search`
- `tmall_chaoshi / maochao_item_export`
- `tmall_chaoshi / statement_bill_dynamic_list`
- `tmall_chaoshi / download_file_query`

旧项目已有两类稳定业务能力：

1. 猫超商品列表更新
   - 优先读本地 `~/Downloads/猫超商品列表导出.xlsx`
   - 缺失或强制刷新时，通过 SessionHub 触发真实导出
   - 用聚水潭商品资料修正新增条码

2. 猫超账单下载
   - 通过 `statement_bill_dynamic_list` 触发导出任务
   - 通过 taskId 或下载查询接口获取真实下载内容
   - 下载 HDB 账单和可选对账单列表

新项目 `Ops-Cli` 当前已具备：

- `CommandResponse` 统一输出
- `logs/app.log` 命令日志
- `runtime/context/*.json` 上下文沉淀
- JST 能力的 `learn/run` 样板
- TMCS 基础 `auth` 命令组，但业务命令仍是 mock

## CLI Surface

新增并固定以下命令：

```bash
ops --json tmcs product learn
ops --json tmcs product sync
ops --json tmcs product sync --dry-run
ops --json tmcs product sync --use-local-only
ops --json tmcs product sync --force-refresh

ops --json tmcs bill learn
ops --json tmcs bill download --start 2026-05-01 --end 2026-05-16
ops --json tmcs bill download --last-month
ops --json tmcs bill download --download-statement-list
ops --json tmcs bill download --dry-run
```

保留现有：

```bash
ops tmcs auth check
ops tmcs auth ensure
ops tmcs auth capture
```

## Approach Options

### Option 1: 推荐，能力层迁移

在 `Ops-Cli` 内重写最小执行层，只复用旧项目的 SessionHub scene 和少量请求模板口径。

优点：

- 新项目边界清晰
- 后续更容易继续迁移 TMCS 能力
- 不受旧任务包装层耦合影响

缺点：

- 需要重新实现一部分执行逻辑

### Option 2: 直接包旧脚本

让 `Ops-Cli` 只是 CLI 壳，内部调用旧项目任务模块。

优点：

- 初期开发快

缺点：

- 破坏新项目独立性
- 后续维护仍耦合旧项目

### Option 3: 全量复制旧 client

把旧项目里猫超 client 大量复制到 `Ops-Cli` 再重构。

优点：

- 最完整

缺点：

- 本轮范围过大
- 不符合“先迁能力层”的节奏

本次采用 Option 1。

## Architecture

### 1. Product Sync Layer

新增模块：

- `src/ops_cli/platforms/tmcs/product.py`

职责：

- `learn_tmcs_product_sync()`
  - 校验并沉淀 `maochao_item_export` / `maochao_item_search` scene 模板
  - 写 `data/tmcs/product_sync_template.json`
  - 写 `runtime/context/tmcs_product_learn_*.json`

- `run_tmcs_product_sync()`
  - 默认优先读取 `~/Downloads/猫超商品列表导出.xlsx`
  - `--force-refresh` 时走后台真实导出
  - 用 JST 商品主表修正新增条码
  - 生成同步结果文件到 `~/Downloads/猫超商品列表导出 (最新）.xlsx`
  - 写 `runtime/context/tmcs_product_sync_run_*.json`

第一阶段不做：

- 递归同步外部同名文件
- 改写旧项目 Excel 模板目录

### 2. Bill Download Layer

新增模块：

- `src/ops_cli/platforms/tmcs/bill.py`

职责：

- `learn_tmcs_bill_download()`
  - 校验并沉淀 `statement_bill_list_for_supplier` / `statement_bill_dynamic_list` / `download_file_query` scene 模板
  - 写 `data/tmcs/bill_download_template.json`
  - 写 `runtime/context/tmcs_bill_learn_*.json`

- `run_tmcs_bill_download()`
  - 支持 `--start/--end` 或 `--last-month`
  - `--last-month` 查询窗口顺延 3 天，避免平台月末账单延迟生成导致漏下载
  - 先通过 `statement_bill_list_for_supplier` 拉 HDB 列表
  - 下载 HDB 账单文件到 `~/Downloads`
  - 通过 `statement_bill_dynamic_list` 触发 `对账单列表.xlsx` 导出任务
  - 按 `taskId -> GEI 下载地址 -> 下载中心兜底` 获取 `对账单列表.xlsx`
  - 写 `runtime/context/tmcs_bill_download_run_*.json`

第一阶段不做：

- 直接跑整月对账整理
- 修改 `06-猫超月对账自动化操作` 目录里的任何文件

### 3. Shared TMCS Utilities

建议新增：

- `src/ops_cli/platforms/tmcs/shared.py`

职责：

- 解析 SessionHub scene
- 构造 TMCS 请求 headers / cookies
- 统一下载到 Downloads
- 提供 taskId 轮询和导出 URL 解析

这样 `product.py` 和 `bill.py` 不会各自重复拼请求逻辑。

## Data Flow

### Product Sync

1. `ops tmcs product learn`
2. 读取旧 SessionHub scene
3. 写本地 template 与 context
4. `ops tmcs product sync`
5. 读本地导出文件，或按需调用真实导出接口
6. 读取 JST 商品主表
7. 修正新增条码和输出文件
8. 写日志与 context

### Bill Download

1. `ops tmcs bill learn`
2. 读取旧 SessionHub scene
3. 写本地 template 与 context
4. `ops tmcs bill download`
5. 按日期构造查询参数
6. 触发导出任务
7. 轮询并下载账单文件
8. 写日志与 context

## Error Handling

必须统一报结构化错误，不返回裸 traceback。

重点错误出口：

- SessionHub scene 缺失
- scene 校验失败
- 本地导出文件不存在
- 导出接口没返回 taskId 或下载地址
- 下载内容不是 Excel
- JST 商品主表缺少关键列
- 下载目录写入失败
- 日期参数非法

所有错误都要输出：

- `success=false`
- `platform=tmcs`
- `command`
- `data.error`
- 尽量附带 `next_command`

## Testing

至少覆盖：

1. `ops tmcs product --help`
2. `ops tmcs product learn`
3. `ops tmcs product sync --dry-run`
4. `ops tmcs product sync --use-local-only`
5. `ops tmcs product sync --force-refresh`
6. `ops tmcs bill --help`
7. `ops tmcs bill learn`
8. `ops tmcs bill download --dry-run`
9. `ops tmcs bill download --last-month`
10. taskId / 下载地址解析
11. 所有命令都支持 `--json`
12. 所有真实执行都写 `runtime/context`

## Scope Boundaries

本轮只完成：

- `Ops-Cli` 中新增 TMCS 商品同步能力
- `Ops-Cli` 中新增 TMCS 账单下载能力
- 文档与测试同步

本轮不做：

- 修改旧自动化项目代码
- 迁移整月账单整理逻辑
- 迁移商品编码同步到外部模板目录
- 新增 GUI / Web 服务 / 数据库
