# 猫超优先推广自动建计划 workflow

`workflow_id`: `tmcs_priority_promotion_plan_create`  
中文入口：`猫超优先推广自动建计划`

读取 `猫超推广清单_<month>.xlsx` 的优先推广段，过滤已在 `正在推广商品列表.xlsx` 中的商品，
解析出 `store_style_code / product_code / item_id`，再逐个调用已有
`tmcs_zdx_fullsite_plan_create` workflow 创建智多星全站推广计划。

## 入口

```bash
python3 run.py workflow tmcs_priority_promotion_plan_create --month 2026-05 --daily-budget 100 --dry-run
python3 run.py 猫超优先推广自动建计划 --month 2026-05 --daily-budget 100 --dry-run
python3 run.py workflow tmcs_priority_promotion_plan_create --month 2026-05 --daily-budget 100 --execute
```

## Steps

1. `check_inputs`：校验月份、默认路径、预算、dry-run/execute、limit
2. `load_priority_promotion_list`：读取优先推广清单；可选先自动生成源文件
3. `load_active_promotion_list`：读取正在推广商品列表
4. `filter_not_active`：过滤已在推广中的店铺款式编码
5. `resolve_item_ids_for_plan`：优先使用源文件自带字段，否则走猫超商品主表映射 `item_id`
6. `build_create_plan_payloads`：构造子 workflow 调用参数
7. `create_zdx_plans`：dry-run 只预览 payload；execute 才逐个调用 `tmcs_zdx_fullsite_plan_create`
8. `sync_active_promotion_list`：真实创建成功后，把成功商品的店铺款式编码去重回写到 `正在推广商品列表.xlsx`
9. `write_outputs`：按需写 JSON / XLSX / CSV
10. `collect_outputs`：汇总计数与最终列表

## dry-run 规则

- 默认 dry-run
- 没有 `--execute` 绝不真实创建
- dry-run 只输出：
  - `priority_items`
  - `skipped_items`
  - `to_create_items`
  - `plan_payloads`
- dry-run 不写原始主数据，不直连平台，不调用真实创建
- 只有 `--execute` 且计划真实创建成功，才会回写 `正在推广商品列表.xlsx`

## 边界

- 真实创建继续走已有 `tmcs_zdx_fullsite_plan_create`
- 本 workflow 不写猫超 URL、Cookie、Token、Selector、Playwright、CDP
- 只会在真实创建成功后追加回写 `正在推广商品列表.xlsx`；不会修改推广清单
