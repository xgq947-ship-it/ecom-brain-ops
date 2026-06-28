# tmcs_marketing_risk_warning — 猫超营销端风险预警数值读取

读取「天猫超市 → 营销 →（新）营销活动中心 → 风险预警（N）」中的预警数值并输出。

## 步骤

| step | 说明 |
|---|---|
| `check_inputs` | 解析 `--dry-run` 等参数 |
| `fetch_risk_warning_count` | 调用 `ops --json tmcs marketing risk-warning count`，dry-run 透传 `--dry-run` |
| `collect_outputs` | 输出 `risk_warning_count / label_text / source`，写入 runtime |

## 输出

- `risk_warning_count`：风险预警数值（页面「风险预警（0）」→ 0，「风险预警（3）」→ 3）
- `label_text`：归一标签文本，如「风险预警（0）」
- `source`：`page`（真实读取）或 `simulated`（dry-run）
- `simulated`：是否为模拟结果

## dry-run 行为

- 向 Ops-Cli 透传 `--dry-run`，平台层返回 `simulated=true` 占位结果（`risk_warning_count=0`）。
- 不做任何写入；不处理、不关闭任何预警；不访问真实猫超页面。

## 边界

- 所有猫超页面访问、营销活动中心入口、风险预警文本读取、SessionHub/CDP/Selector/Playwright
  均在 Ops-Cli `tmcs marketing risk-warning count`。
- 本 workflow 不含任何猫超 URL、Cookie、Token、Selector、Playwright、CDP。
- 失败错误码（由 Ops-Cli 返回）：`AUTH_REQUIRED` / `SCENE_CAPTURE_FAILED` /
  `PLATFORM_REQUEST_FAILED` / `RISK_WARNING_COUNT_NOT_FOUND`。

## 命令

```bash
python3 run.py workflow tmcs_marketing_risk_warning --dry-run
python3 run.py 猫超营销风险预警 --dry-run
```

## 真实读取状态

已用双浏览器学习核对真实入口并跑通（2026-06）：
- frameUrl：`txcs.portal.tmall.com/mmc-market-across/pages/TmallActivityHomePage/index.html`
- 「重要事项」卡片含「风险预警 N」徽标（DOM 文本形如 `风险预警\n0`）
- 实测输出：`risk_warning_count=0, label_text=风险预警（0）, source=page`

若后续平台改版导致 `RISK_WARNING_COUNT_NOT_FOUND`，在主浏览器重新打开
「营销 →（新）营销活动中心」核对入口，更新 `Ops-Cli/.../tmcs/marketing.py` 的
`TMCS_MARKETING_CENTER_FRAME_URL` 即可。
