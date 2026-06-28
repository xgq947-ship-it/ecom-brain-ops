# tmcs_fulfillment_watch — 猫超物流履约监控

属"平台读取 + workflow 业务判断"类型功能：

- 平台读取（首页 → 天机 → 商家仓履约 → 日常考核 → 数据概览）全部由 Ops-Cli
  `ops --json tmcs fulfillment overview` 完成。本 workflow 不写猫超 URL、Cookie、
  Token、Selector、Playwright、CDP，也不直接请求平台。
- 本 workflow 只做：考核/观测指标判断、接近预警判断、周数据预警等级透传、通知预览。

## 入口

```bash
python3 run.py workflow tmcs_fulfillment_watch --dry-run
python3 run.py workflow tmcs_fulfillment_watch --warning-margin 2 --dry-run
python3 run.py workflow tmcs_fulfillment_watch --simulate-risk --dry-run        # 预览预警文案
python3 run.py 猫超履约监控 --dry-run
python3 run.py 猫超履约监控 --warning-margin 2 --dry-run
```

## 参数

- `--dry-run`：向 Ops-Cli 透传 `--dry-run`，平台层返回 `simulated=true`，不访问真实猫超；通知只预览不发送。
- `--notify`：允许在真实风险时发送通知（仍受 dry-run 抑制：dry-run 只预览）。
- `--warning-margin`：接近预警的容差，默认 2，非负。
- `--simulate-risk`：**仅 dry-run** 下用本地风险样本覆盖指标，便于预览预警文案；不碰平台。

## 步骤

1. `check_inputs` — 解析 `--dry-run / --notify / --warning-margin / --simulate-risk`，校验。
2. `fetch_fulfillment_overview` — 调 `ops --json tmcs fulfillment overview`，校验 `metrics`；
   dry-run + `--simulate-risk` 时用本地风险样本覆盖。Ops-Cli 失败返回清晰错误。
3. `evaluate_metrics` — 指标判断，产出 `risk_items`：
   - 只对三个核心指标做预警：24H支揽率≥95、送货上门≥75、隔日达≥55。
   - 考核/观测「≥阈值」类：
     `值 < 阈值` → `fail`；`阈值 ≤ 值 ≤ 阈值 + margin` → `near`。
   - 48H支揽率 / 表达签准率 / 4CP占比 / 4CP占比_剔偏远 / 支签时长：观测/记录项，默认不预警。
   - `平均支签时长`：只记录，不预警。
   - `履约异常单反馈`：当天有异常单（`exception_feedback_required=true`）→ `action`。
   - `周数据预警等级`（A/B/C，来自平台）非空 → `weekly`。
4. `build_warning_message` — `risk_items` 非空才生成预警文案；为空不产文案。
5. `notify_if_needed` — 统一走 `core.runtime.send_notification(content, dry_run=...)`；
   无风险不发；有风险但未 `--notify` 只记录预览；dry-run 只预览。
6. `collect_outputs` — 输出 `metrics / risk_items / warning_level / should_notify /
   warning_message / notification / source / simulated / scene / ops_context_path`。

## dry-run 行为

- 不访问真实猫超（平台层 `simulated=true`）。
- 不真实发送任何通知，只产 `preview`。
- `--simulate-risk` 仅用于预览风险路径，数据为本地样本。

## 产物

当前默认不落本地结果文件，因此默认不产 Artifact；如后续需要导出履约结果，
再用 `core.runtime.Artifact` 记录（`role=output`，`platform=tmcs`）。

## 边界

平台访问、SessionHub、CDP、Selector、平台 URL 全在 Ops-Cli。真实模式已跑通：Ops-Cli
用 9222 + Playwright 读「日常考核」页卡片与「考核表现」横幅（含周预警等级）；解析不到时
返回 `FULFILLMENT_OVERVIEW_NOT_FOUND`。指标按真实页面口径为 4CP 占比（非 7CP）。
