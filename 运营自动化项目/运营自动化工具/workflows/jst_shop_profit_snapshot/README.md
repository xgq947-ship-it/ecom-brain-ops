# jst_shop_profit_snapshot workflow

聚水潭店铺利润快照：经 Ops-Cli 拉取店铺利润明细（默认昨天，`--date` 取任意单日，`--month` 取月度），
写出一份结构化 JSON 快照，供后续报表/飞书推送复用。

## 入口

旧中文命令（走 `tasks/jst_shop_profit_snapshot.py` 薄 wrapper）：

```bash
python3 run.py 聚水潭店铺利润快照
python3 run.py 店铺利润快照 --dry-run
python3 run.py 猫超月利润 --month 2026-05
```

新 workflow 入口：

```bash
python3 run.py workflow jst_shop_profit_snapshot --dry-run
python3 run.py workflow jst_shop_profit_snapshot --date 2026-06-15
python3 run.py workflow jst_shop_profit_snapshot --month 2026-05
python3 run.py workflow jst_shop_profit_snapshot --shop qiming --output /abs/path/profit.json
```

支持参数：`--date YYYY-MM-DD`（任意单日，也接受 `today`/`yesterday`；与 `--month` 互斥）、`--month YYYY-MM`（两者都不传则取昨天）、`--shop`、`--output`（覆盖默认快照路径）、`--metrics 营销费用,财务费用,毛利率`（按名称从全部利润科目与 KPI 里挑选）、`--full`（outputs 额外带完整 37 条利润科目）、`--dry-run`。

## 月度店铺财务（营业额 / 营销费用 / 财务费用 …）

数据源是聚水潭「经营利润多维度报表」，`--detail` 会返回整张报表的全部科目行。
outputs 与快照 JSON 里的 `financial_summary` 已从中挑出关键科目（按科目编码精确匹配，
父子科目不串扰）：

| label | 来源科目 |
|---|---|
| 销售收入 / 付款金额 | 顶层营收行 |
| 销售成本 / 毛利额 | 成本与毛利 |
| 销售费用 | `6601` |
| 营销费用 | `660101` |
| 平台费用 | `660102` |
| 店铺直接运营费用 | `660103` |
| 售后费用 | `660104` |
| 人工干预费用 | `660105` |
| 管理费用 | `6603` |
| 财务费用 | `6604` |
| 快递费用 | `6605` |
| 经营利润 | 经营利润 |

## 运营 KPI（kpi_summary，默认输出）

除利润科目外，outputs 与快照 JSON 还有 `kpi_summary`，从报表 `summaryData` 结构化挑出运营 KPI（不含「退货后/ByReturn」系列，匹配不到的 key 跳过容错）：

| label | 来源 key |
|---|---|
| 毛利率 | `grossProfitRate` |
| 退款率(发货前) | `refundratePre` |
| 退款率(发货后) | `refundrateAfter` |
| 单量 | `billQuantity` |
| 客单价 | `avgBillSalePrice` |
| 单均件数 | `avgBillQuantity` |
| 商品件数 | `goodsQuantity` |
| 倍率 | `priceMultiple` |
| 件均成本 | `avgSkuCostAmount` |

## 按需取 / 全量

- `--metrics 名称1,名称2`：从**利润科目 + KPI**里按名称包含匹配（含叶子项，如 `万相台`、`毛利率`、`客单价`），结果放 outputs 的 `selected_metrics`（KPI 命中项带 `"kind": "kpi"`）。
- `--full`：outputs 额外带上完整 37 条 `metrics`（默认只给 `financial_summary` + `kpi_summary` 摘要，避免撑大上下文）。
- 接口原始大块 `raw_data` / `raw_response`（约 433KB）只存进快照文件，不进 outputs；要原始数据直接读快照 JSON。

```bash
python3 run.py workflow jst_shop_profit_snapshot --month 2026-06 --metrics 营销费用,毛利率,客单价
python3 run.py workflow jst_shop_profit_snapshot --month 2026-06 --full
```

匹配不到的科目会被跳过（容错），不影响原始 `metrics` 与快照其余字段。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析 `--date/--month/--shop/--output/--dry-run`，判定 period（`--date`+`--month` 同时给报错） | 只解析 |
| `fetch_profit_detail` | 经 Ops-Cli 调 `jst profit day/month/yesterday --detail` 拉利润明细 | **不调用平台，只回显计划 `ops_command`**；失败返回 `failure_result` |
| `write_snapshot` | 把利润明细写成 JSON 快照并登记 Artifact | **不写文件，只回显 `planned_output_path`** |
| `collect_outputs` | 汇总 store/profit/metric 到 outputs | 同左 |

## dry-run 安全策略

1. `fetch_profit_detail` 在 dry-run 下直接返回 `planned=True` + 计划执行的 `ops_command`，**不调用 `run_ops_json`**，不触发真实聚水潭请求与短信授权。
2. `write_snapshot` 在 dry-run 下不落盘，只回显 `planned_output_path`，**不写正式快照文件**。
3. 默认快照写到 `runtime/artifacts/jst_shop_profit_snapshot/`，真实执行时登记 `Artifact`。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作经 `clients/ops_cli_client.py` 调 `ops --json jst profit ...`，在 `steps.py` 内完成。
- 业务编排在 `steps.py`；`tasks/jst_shop_profit_snapshot.py` 仅为透传薄 wrapper。
