# tmcs_xp_workorder_watch — 猫超 XP 工单数量监控

## 用途

读取猫超首页「基础待办」卡片中的 `XP工单处理 紧急(n)` 数量，与阈值比较。超过阈值时输出告警文案；否则输出当前数量与“无需处理”状态。真实通知由外层 watchdog wrapper 负责，workflow 只返回结果。

## 调用链

```
run.py workflow tmcs_xp_workorder_watch
  └─ workflows/tmcs_xp_workorder_watch/workflow.py
       └─ clients/ops_cli_client.run_ops_json
            └─ ops --json tmcs xp-workorder count --threshold N [--dry-run]
                 └─ Ops-Cli tmcs/xp_workorder.py  (读取猫超首页 DOM 文本)
```

平台 URL/Cookie/Selector/Playwright/CDP 全部封装在 Ops-Cli；business 层只消费 JSON。

## 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--threshold` | `4` | 工单数量阈值，`count > threshold` 视为超过 |
| `--dry-run` | False | 安全预览：Ops-Cli 不访问首页，返回 simulated=true |
| `--notify` | False | 兼容参数；workflow 不真实推送，只在 outputs 里标记应由外层 wrapper 发送 |
| `--json` | False | 仅占位，run.py 不消费 |

## 步骤

| step | 行为 | dry-run 行为 |
|---|---|---|
| `check_inputs` | 解析 `--threshold/--dry-run/--notify` | 同实模式 |
| `fetch_workorder_count` | 调 `ops --json tmcs xp-workorder count` | 透传 `--dry-run`，Ops-Cli 返回 simulated=true、count=0 |
| `evaluate_threshold` | 计算 `exceeded = count > threshold`，生成中文文案 | 同实模式（基于模拟数量） |
| `collect_outputs` | 汇总最终输出 | 标记 `dry_run=true, simulated=true`；notification 只返回委托状态，不真实发送 |

## 输出字段（最终 step）

```jsonc
{
  "task": "tmcs_xp_workorder_watch",
  "dry_run": true,
  "count": 0,
  "threshold": 4,
  "exceeded": false,
  "message": "当前猫超 XP 工单数量：0，未超过阈值 4",
  "source": "simulated",        // simulated | dom
  "simulated": true,
  "scene": "tmall_chaoshi/xp_workorder_count",
  "ops_context_path": "...",
  "notification": {"sent": false, "reason": "通知未启用"}
}
```

## 边界

- 不直接打猫超 URL，不读 Cookie/Token，不写 Playwright/CDP。
- 不修改 session/cookie/token 文件。
- workflow 实模式也不直接发企微；真实通知统一由外层 watchdog wrapper 发送。
- dry-run 绝不发送企微/微信、不处理工单、不写 Excel。
- 失败语义：`fetch_workorder_count` 失败即终止 workflow，输出 `errors`。

## 当前实现口径

- `Ops-Cli` 真实执行时会打开 `https://web.txcs.tmall.com/`
- 直接从首页正文提取 `XP工单处理 紧急(n)`
- 当前实测有效值为首页卡片里展示的紧急数量，不再使用“近 365 天总工单”等接口字段
- `ops --json tmcs xp-workorder learn` 仅保留兼容入口，返回“无需 scene 学习”的说明，不再抓接口结构

兼容命令：

```bash
ops --json tmcs xp-workorder count
ops --json tmcs xp-workorder count --threshold 4
ops --json tmcs xp-workorder count --dry-run
ops --json tmcs xp-workorder learn
```
