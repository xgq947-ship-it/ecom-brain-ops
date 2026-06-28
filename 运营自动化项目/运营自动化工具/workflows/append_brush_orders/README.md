# append_brush_orders workflow

把「刷单表格登记」从单脚本升级为 step 化流程。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/append_brush_orders.py`）：

```bash
python3 run.py 刷单表格登记 --dry-run
python3 run.py 刷单表格登记 昨天的
```

新 workflow 入口：

```bash
python3 run.py workflow append_brush_orders --dry-run
python3 run.py workflow append_brush_orders            # 真实追加 + 触发打标
```

支持参数（透传给复用逻辑）：`--work-dir`、`--source-dir`、`--product-file`、`--brush-product-file`、`--no-auto-fetch-wechat`、`--wechat-file-dir`、`--wechat-target-dir`、`--wechat-date`、`--print-skipped-wechat`、位置日期词（如 `昨天的`、`4月29`）、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数、`configure_paths` 配置路径 | 只配置 |
| `load_source_orders` | 检查源表目录是否已有 xlsx | 只读 |
| `validate_orders` | 校验聚水潭商品资料 / 刷手商品表存在 | 只读 |
| `append_to_register` | 整体编排同包 `appender.run()` 完成识别/去重/追加 | 只预览，不写登记表 |
| `collect_artifacts` | 汇总追加结果、最新订单号文件 | 只汇总 |

> append 主体逻辑（多格式解析、去重、登记表 ZIP/XML 追加、聚水潭打标触发）耦合较深，由同包 `appender.run()` 整体承载，后续可再细拆。

## dry-run 安全策略

1. **不写登记表**：`append_to_register` 以 `dry_run=True` 调 `appender.run()`，其中登记表追加（`patch_workbook`/`append_plain_workbook`）由 `not dry_run` 守卫。
2. **不预检 / 不打标 / 不清源**：`preflight_platform_auth`、`trigger_jst_tagging`、`clear_source_dir`、`write_latest_brush_orders` 均仅在真实执行时运行。
3. **微信文件只读预览**：workflow dry-run 会扫描微信目录并直接读取匹配源表，但不复制到 `今日刷单表格`，避免把“微信里有文件”误报成“没有源表”。
4. 重复订单去重逻辑在 `appender.py`（按来源 mtime 保留最新、跳过登记表已存在单号）。
5. Excel 追加沿用 `appender.py` 的 ZIP/XML 局部补丁（`patch_workbook`），保留含图片结构的登记表。

## 真实执行

去掉 `--dry-run`：自动复制微信源表 → 识别/去重 → 追加登记表 → 写最新订单号 → 触发聚水潭打标 → 清空源目录。

## 边界

- 平台动作（打标）经 `tasks/jst_order_label`（→ `clients/ops_cli_client.py` → Ops-Cli）触发；workflow 不直接请求平台。
- 业务逻辑在同包 `appender.py`（`run / configure_paths / read_all_source_batches / patch_workbook` 等）；`tasks/append_brush_orders.py` 仅为透传薄 wrapper。
