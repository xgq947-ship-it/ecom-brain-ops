# 迁移报告

## 本次迁移做了什么

### 1. 平台调用统一下沉到 Ops-Cli

已改为 `Ops-Cli` 调用的任务：

- `tasks/jst_product_sync/main.py`
- `tasks/jst_order_label/main.py`
- `tasks/jst_brush_reimburse_workorder.py`
- `tasks/tmall_product_list/main.py`
- `tasks/tmall_monthly_bill/downloader.py`
- `skills/tmcs_sync_jst_shop_goods/*` 继续保留 `Ops-Cli` 调用方式并同步文档

本次复检确认并清理：

- 删除旧兼容包装入口：`tasks/tag_jst_brush_orders.py`
- 删除旧兼容包装入口：`tasks/update_jst_products.py`
- 删除旧兼容包装入口：`tasks/update_maochao_goods.py`
- 删除旧兼容包装入口：`tasks/process_maochao_bills_downloads.py`
- `append_brush_orders` 自动打标改为直接调用 `tasks/jst_order_label/main.py`
- `tmall_monthly_bill` 移除旧 Copy as cURL fallback 参数
- `jst_brush_reimburse_workorder` 改为调用 `ops --json jst order reimburse`
- 删除旧平台 client：`clients/jst_client.py`
- 删除旧平台 client：`clients/sessionhub_client.py`

### 2. 文档同步

已同步：

- `Ops-Cli/README.md`
- `Ops-Cli/docs/architecture.md`
- `Ops-Cli/docs/platform_capabilities.md`
- `Ops-Cli/docs/double_browser_learning.md`
- `Ops-Cli/docs/browser_9222_debug.md`
- `Ops-Cli/docs/ops_cli_contract.md`
- `运营自动化工具/README.md`
- `运营自动化工具/SKILL.md`
- `运营自动化工具/docs/architecture.md`
- `运营自动化工具/docs/project_boundary.md`
- `运营自动化工具/docs/skill_development_spec.md`
- `运营自动化工具/docs/ops_cli_integration.md`

### 3. 配置同步

新增配置：

- `ops_cli_root`
- `ops_cli_bin`

位置：

- `config/paths.yaml`
- `core/config_loader.py`

### 4. 命令风格统一

统一到：

- `ops --json tmcs ...`
- `ops --json jst ...`
- `ops --json browser ...`

业务层不再把平台脚本名当正式命令设计。

### 5. 平台能力执行契约统一

- `Ops-Cli` 已增加 capability registry 与统一 runner，纳管现有 TMCS、JST 和 browser 命令。
- stdout 严格输出单个 JSON 文档；登录等待、浏览器启动和恢复过程仅输出 stderr 与 context。
- 真实 `jst` / `tmcs` 业务请求会由公共客户端先按平台执行一次 `--interactive-login ... auth ensure`，因此后台运行也可预检并尝试恢复已有 9222 登录态；`--dry-run` 与 `auth` 命令不触发前置预检。
- 业务层通过 `clients/ops_cli_client.py` 消费 `error_code`、`context_path` 与 `session_recovery`，不解析浏览器提示文案。
- `Ops-Cli/sessionhub` 的代码与配置进入版本管理，Cookie、session 和日志仍为本地忽略资产。

## 之前的架构冲突

- 运营项目里存在直接请求 JST / TMCS 的任务实现
- 运营项目 README / SKILL / docs 仍把 SessionHub 和平台实现写成业务层职责
- 任务文档与实际 CLI 漂移
- `Ops-Cli` 缺少完整的平台能力说明、9222 说明、双浏览器说明、调用契约说明

## 当前新架构

```text
运营自动化工具
  -> 业务任务
  -> ops_cli_client
  -> Ops-Cli
  -> SessionHub 资产
```

## 已删除 / 已废弃的重复口径

- 运营项目中的平台文档主说明已废弃，改为指向 `Ops-Cli/docs/`
- 运营项目中直接写平台调用的 README 口径已废弃，改为 `Ops-Cli` 调用口径

## 仍保留的兼容项

- `sessionhub/` 会话资产目录已迁移到 `Ops-Cli/sessionhub`
- 业务层不再保留 JST / SessionHub 直连 client；平台接口统一由 `Ops-Cli` 承接
