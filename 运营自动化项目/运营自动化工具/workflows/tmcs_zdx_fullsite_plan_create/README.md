# tmcs_zdx_fullsite_plan_create workflow

根据商品ID创建智多星货品全站推广计划。

## 页面路径

天猫超市首页 → 推广 → 推广平台 → 智多星 → 点击前往智多星
→ 货品全站推 → 创建计划 → 填写计划名称、商品ID、每日预算、目标ROI → 确认创建

## 入口命令

```bash
# 预览（dry-run，默认安全）
python3 run.py workflow tmcs_zdx_fullsite_plan_create --item-id 123456789 --daily-budget 100 --dry-run

# 中文入口 dry-run
python3 run.py 创建智多星全站推广计划 --item-id 123456789 --daily-budget 100 --dry-run

# 真实创建（必须显式传 --execute 和 --confirm-plan-name）
python3 run.py workflow tmcs_zdx_fullsite_plan_create \
  --item-id 123456789 --daily-budget 100 \
  --execute --confirm-plan-name 全站推广_123456789_0602

# 手动指定 ROI（跳过自动查询）
python3 run.py workflow tmcs_zdx_fullsite_plan_create \
  --item-id 123456789 --daily-budget 100 --roi 3.5 --dry-run
```

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--item-id` | 是 | 天猫商品ID（= 猫超商品列表中的商品编码） |
| `--daily-budget` | 是 | 每日预算金额（元），必须 > 0 |
| `--dry-run` | - | 安全预览，不真实创建计划 |
| `--execute` | 真实执行时必填 | 真实创建计划，必须显式传入 |
| `--plan-name` | 否 | 自定义计划名称；默认 `全站推广_{item_id}_{mmdd}` |
| `--roi` | 否 | 手动指定目标投产比；不传则自动从 Excel 查询理想ROI |
| `--confirm-plan-name` | 真实执行时必填 | 必须等于最终 plan_name，作为二次确认 |

## Steps

1. `check_inputs` — 校验 item_id、daily_budget、execute/confirm_plan_name 安全锁
2. `build_plan_name` — 生成计划名称（默认 `全站推广_{item_id}_{mmdd}`）并校验 confirm_plan_name
3. `resolve_target_roi` — 从 Excel 查理想ROI（商品ID作为商品编码查猫超商品列表→条码→聚水潭→计算）
4. `preview_plan_payload` — 输出最终计划参数预览
5. `create_zdx_plan` — 调用 `ops tmcs zdx fullsite-plan create`（dry-run 传 `--dry-run`，真实传 `--execute`）
6. `collect_outputs` — 汇总结果，记录 Artifact

## ROI 查询链路

```
item_id（= 商品编码）
  → 猫超商品列表 Excel（find_tmcs_barcode product_code=item_id）
  → 条码
  → 聚水潭商品资料 Excel（find_jst_product）
  → 淘系控价 + 成本价
  → calculate_roi → ideal_roi（理想ROI）
```

ROI 查询失败时返回清晰错误，不允许继续创建计划。  
可用 `--roi` 手动指定目标投产比绕过自动查询。

## dry-run 行为

- 不访问天猫/智多星页面
- 不点击确认创建
- 不发送任何通知
- 输出完整计划参数预览（item_id、plan_name、daily_budget、target_roi）
- Ops-Cli 返回 `executed=false, created=false, simulated=true`

## 安全规则

1. 默认永远 dry-run
2. 没有 `--execute` 绝不调用真实创建
3. 有 `--execute` 但无 `--confirm-plan-name` → 失败
4. `confirm_plan_name ≠ plan_name` → 失败
5. ROI 获取失败 → 失败，不允许继续

## 产物

dry-run 无文件产物，Ops-Cli context JSON 记录在 runtime。  
真实执行成功时若平台返回 `platform_plan_id` 则记录在 outputs。

## 架构边界

- 本 workflow 不含猫超 URL、Cookie、Token、Selector、Playwright、CDP
- 平台操作全部由 `ops tmcs zdx fullsite-plan create` 完成
- ROI 计算直接复用 `workflows.tmcs_sku_roi.excel_lookup` 和 `roi_calculator`（不调用 tmcs_sku_roi workflow 本身）
