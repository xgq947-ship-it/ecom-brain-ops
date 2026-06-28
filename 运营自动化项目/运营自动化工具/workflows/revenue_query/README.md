# revenue_query — 今日实时营业额 workflow

今日实时营业额：经 Ops-Cli 读取聚水潭订单统计，返回指定店铺、指定日期的订单数与已付金额。
默认查「今天 + 猫超(qiming)店铺」。纯读取，不写任何文件。

## 入口

旧中文命令（走 `tasks/revenue_query.py` 薄 wrapper）：

```bash
python3 run.py 今日实时营业额
python3 run.py 猫超今日实时营业额
python3 run.py 营业额查询
python3 run.py 猫超今日营业额
python3 run.py 今日实时营业额 --date 2026-06-17 --shop qiming
python3 run.py 今日实时营业额 --dry-run
```

新 workflow 入口：

```bash
python3 run.py workflow revenue_query --dry-run
python3 run.py workflow revenue_query --date today --shop qiming
```

支持参数：`--date`（默认 `today`）、`--shop`（默认 `qiming`）、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析 `--date/--shop/--dry-run` | 只解析 |
| `fetch_order_stats` | 经 Ops-Cli 调 `jst order stats` 查营业额 | **不调用平台，只回显 `ops_command`** |
| `collect_outputs` | 汇总 store/order_count/paid_amount 到 outputs | 同左 |

## dry-run 安全策略

1. `fetch_order_stats` 在 dry-run 下直接返回 `planned=True` + 计划执行的 `ops_command`，**不调用 `run_ops_json`**，不触发真实聚水潭请求与短信授权。
2. 本 workflow 纯读取，不写 Excel/CSV/JSON，无文件产物。
3. Ops-Cli 调用失败时返回 `failure_result`，给出清晰错误信息。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作经 `clients/ops_cli_client.py` 调 `ops --json jst order stats`，在 `steps.py` 内完成。
- 业务编排在 `steps.py`；`tasks/revenue_query.py` 仅为透传薄 wrapper。
