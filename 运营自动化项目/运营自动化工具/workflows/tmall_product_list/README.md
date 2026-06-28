# tmall_product_list workflow

把「更新猫超商品列表」从单脚本升级为 step 化流程。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/tmall_product_list/main.py`）：

```bash
python3 run.py 更新猫超商品列表 --dry-run
python3 run.py 更新猫超商品列表 --dry-run --skip-auto-download
```

新 workflow 入口：

```bash
python3 run.py workflow tmall_product_list --dry-run
python3 run.py workflow tmall_product_list --dry-run --skip-auto-download
python3 run.py workflow tmall_product_list            # 真实同步并写主表
```

支持参数（透传给复用逻辑）：`--skip-auto-download`（→ ops `--use-local-only`）、`--force-refresh`、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数 | 只解析 |
| `check_local_source` | 检查本地导入表是否存在 | 只读 |
| `download_tmcs_products` | 经 Ops-Cli 同步猫超商品（`tmcs product sync`） | 透传 `--dry-run`，仅预览 |
| `validate_products` | 汇报同步摘要 | 只读 |
| `update_master_data` | 写主表/最新表（含上下架状态回写） | **跳过：Ops-Cli `--dry-run` 不写主表** |
| `collect_artifacts` | 汇总产物 | 只汇总 |

## 同步策略：条码 vs 上下架状态

合并最新主表（Ops-Cli `_sync_product_workbook`）时，两类字段处理方式不同：

- **条码**：仅对**新增行**修正（用聚水潭商品编码 exact/fuzzy 匹配回写），存量行条码不动。统计：`exact_replaced / fuzzy_replaced / unchanged / empty_barcode / missed / multiple_candidates`。
- **上下架状态（商品上下架状态 / SKU上下架状态）**：**新增行与存量行都更新**。以（商品编码, SKU编码）为键，用本次下载的最新数据回写存量行的两个状态列；新增行本身已携带最新状态。统计：`status_updated`（存量行被刷新数）、`status_unchanged`（匹配但状态未变）、`status_not_in_import`（存量行不在本次下载结果中，保持原值）。
  - 缺状态列的老结构 / 测试精简表头会安全跳过状态回写，不报错。

## dry-run 安全策略

1. **不下载真实平台文件、不覆盖主数据**：向 ops 透传 `--dry-run`，Ops-Cli `tmcs product sync --dry-run` 只预览，不写最新主表。
2. `interactive_recovery` 由公共客户端按 `--dry-run` 自动判定为 False，dry-run 不拉起浏览器登录。
3. 保留 `--skip-auto-download`（→ `--use-local-only`）、`--force-refresh` 旧参数行为。
4. 产物（最新主表）以 `Artifact` 记录（`role=master_latest, platform=tmcs`），仅真实执行时写入。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作全部经 `clients/ops_cli_client.py` 调 `ops --json tmcs product sync`（由 legacy 完成）。
- 复用 `tasks/tmall_product_list/main.py` 的 `run_ops_json` 与配置路径，不重写同步算法。
