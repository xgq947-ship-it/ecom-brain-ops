# jst_order_label workflow

把「聚水潭刷单订单插黄旗/打标」从单脚本升级为 step 化流程。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/jst_order_label/main.py`）：

```bash
python3 run.py 刷单订单插黄旗 --dry-run --limit 1
python3 run.py 刷单订单插黄旗 --order-id ORDER001
```

新 workflow 入口：

```bash
python3 run.py workflow jst_order_label --dry-run
python3 run.py workflow jst_order_label --limit 1 --dry-run
python3 run.py workflow jst_order_label --order-id ORDER001          # 真实打标
```

支持参数（透传给复用逻辑）：`--order-id`（可重复）、`--input`、`--limit N`、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数、确定输入路径 | 只解析 |
| `load_orders` | 判定订单来源（order-id / input 文件） | 只记录 |
| `preview_labels` | 经 Ops-Cli 查询订单（**不带 `--execute`**） | 执行只读查询 |
| `apply_labels` | 经 Ops-Cli 执行打标（带 `--execute`） | **跳过，不真实打标** |
| `collect_outputs` | 汇总结果、失败文件、runtime_context | 只汇总 |

## dry-run 安全策略

1. dry-run **永不追加 `--execute`**：Ops-Cli `jst order label` 不带 `--execute` 即只查询/预览，不写卖家备注、不插黄旗。
2. dry-run 下 `interactive_recovery=False`，不会拉起浏览器登录。
3. 真实打标必须**非 dry-run**（`apply_labels` 才追加 `--execute`，`interactive_recovery=True`）。
4. 失败订单信息（`failed_file`）由 Ops-Cli 返回并写入 `outputs`，可供后续 retry。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作全部经 `clients/ops_cli_client.py` 调 `ops --json jst order label`（由 legacy 完成）。
- 复用 `tasks/jst_order_label/main.py` 的 `run_ops_json` 与默认输入路径，不重写打标算法。
