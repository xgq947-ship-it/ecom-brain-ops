# jst_pickup_watch workflow

把「聚水潭揽收监控」从单脚本执行升级为 step 化流程执行。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/jst_pickup_watch.py`）：

```bash
python3 run.py 聚水潭揽收监控 --dry-run
python3 run.py 聚水潭揽收监控 --hours 48 --dry-run
python3 run.py 聚水潭揽收监控 --notify
```

新 workflow 入口：

```bash
python3 run.py workflow jst_pickup_watch --dry-run
python3 run.py workflow jst_pickup_watch --hours 48 --dry-run
python3 run.py workflow jst_pickup_watch --notify        # 真实执行 + 有异常时发微信
```

支持参数（透传给复用逻辑）：`--hours N`、`--notify`、`--debug`、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析 `--hours/--notify/--debug/--dry-run` | 只解析 |
| `load_config` | 读取 `config/pickup_watch.json`，确定检查小时数 | 只读 |
| `fetch_pickup_watch_data` | 经 Ops-Cli 拉取近 N 小时付款订单与轨迹 | 透传 `--dry-run`，平台层用模拟订单，**不请求真实聚水潭** |
| `analyze_abnormal_orders` | 复用 `evaluate_orders` 计算风险并生成提醒文案 | 纯计算 |
| `notify_if_needed` | 有异常时按策略发送微信 | **只产出 preview，绝不发送真实微信** |
| `collect_outputs` | 汇总异常订单号、计数、通知结果到 outputs | 同左 |

## dry-run 安全策略

1. `fetch_pickup_watch_data` 向 Ops-Cli 透传 `--dry-run`，平台层返回模拟订单，不触发真实聚水潭请求、不触发短信授权。
2. `notify_if_needed` 在 dry-run 下只把提醒文案放进 `preview`，**不调用 `send_wecom`**。
3. 不生成任何 Excel/CSV（与旧任务一致）；异常订单结果只写入 `outputs`。
4. 正式通知策略与旧任务完全一致：仅当存在异常订单且 `--notify`（非 dry-run）时才真实推送。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作经 `clients/ops_cli_client.py` 调 `ops --json jst order pickup-watch`，在 `steps.py` 内完成。
- 风险评估、提醒文案等纯业务逻辑在同包 `risk_evaluator.py`（`load_config / evaluate_orders / build_notification_content` 等）；通知经 `core.runtime.notify`。`tasks/jst_pickup_watch.py` 仅为透传薄 wrapper。
