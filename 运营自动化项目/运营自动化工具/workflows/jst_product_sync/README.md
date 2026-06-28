# jst_product_sync workflow

把「更新聚水潭商品资料」从单脚本升级为 step 化流程。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/jst_product_sync/main.py`）：

```bash
python3 run.py 更新聚水潭资料 --dry-run
python3 run.py 更新聚水潭资料 --dry-run --use-local-only
python3 run.py 更新聚水潭资料 --keep-brands 奥克斯 苏泊尔
```

新 workflow 入口：

```bash
python3 run.py workflow jst_product_sync --dry-run
python3 run.py workflow jst_product_sync --dry-run --use-local-only
python3 run.py workflow jst_product_sync            # 真实同步并写主数据
```

支持参数（透传给复用逻辑）：`--use-local-only`、`--keep-brands B1 B2`、`--no-filter`、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数 | 只解析 |
| `check_local_source` | 检查本地源文件是否存在 | 只读 |
| `download_jst_products` | 经 Ops-Cli 同步（`jst product sync`） | 透传 `--dry-run`，仅预览 |
| `validate_products` | 汇报同步目标 | 只读 |
| `update_master_data` | 写主数据 | **跳过：Ops-Cli `--dry-run` 不覆盖主数据** |
| `collect_artifacts` | 汇总产物 | 只汇总 |

## dry-run 安全策略

1. **不请求真实聚水潭写入、不覆盖主数据**：向 ops 透传 `--dry-run`，Ops-Cli `jst product sync --dry-run` 只预览。
2. `interactive_recovery` 按 `--dry-run` 自动判定为 False，dry-run 不拉起浏览器。
3. 保留 `--use-local-only`、`--keep-brands`、`--no-filter` 旧参数行为。
4. dry-run 容忍平台/数据未就绪（缺源文件、AUTH）时降级为安全预览。
5. 最新主数据以 `Artifact` 记录（`role=master_latest, platform=jst`），仅真实执行写入。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作全部经 `clients/ops_cli_client.py` 调 `ops --json jst product sync`（由 legacy 完成）。
- 复用 `tasks/jst_product_sync/main.py` 的 `run_ops_json` 与配置路径，不重写同步算法。
