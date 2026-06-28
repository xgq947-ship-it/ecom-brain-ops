# tmcs_product_code_lookup — 猫超商品编码查询

输入商品型号（可选品牌），从**本地**猫超商品列表主数据中筛选上架商品，按型号/条码/商品名称等字段做模糊匹配，输出对应的**猫超商品编码**及相关字段。

这是一个纯本地 Excel 查询 workflow：不请求猫超后台、不请求聚水潭后台、不写 Cookie/Token/Selector/Playwright/CDP、不修改原始 Excel。

## 用法

```bash
# 仅型号
python3 run.py workflow tmcs_product_code_lookup --model "AUXxxx" --dry-run

# 带品牌
python3 run.py workflow tmcs_product_code_lookup --brand "奥克斯" --model "AUXxxx" --dry-run

# 中文入口
python3 run.py 猫超商品编码查询 --brand "奥克斯" --model "AUXxxx" --dry-run
```

## 参数

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--model` | 是 | — | 商品型号或型号关键词 |
| `--brand` | 否 | 无 | 品牌，命中时用于过滤/提高匹配 |
| `--source-file` | 否 | 主数据「猫超商品列表导出 (最新）.xlsx」 | 自定义数据源 |
| `--limit` | 否 | 10 | 最多返回结果数 |
| `--min-score` | 否 | 0.5 | 最低匹配分数（0~1） |
| `--by-sku` | 否 | — | 退回 SKU 粒度，列出同一商品编码下的每个 SKU |
| `--output` | 否 | 无 | 输出结果文件（`.json` 或 `.xlsx`） |
| `--dry-run` | 否 | — | 安全预览，不写出文件 |

> **默认按猫超商品编码去重**：同一商品编码只保留匹配分最高的一条（一个商品编码挂多个 SKU 时不会重复出现）。需要看每个 SKU 明细时加 `--by-sku`。

## 步骤

1. **check_inputs** — 校验 `--model` 必填、`--source-file` 存在、`--limit`/`--min-score` 合法。
2. **load_tmcs_products** — 只读 Excel，自动识别表头，仅保留「商品上下架状态=上架」。必需字段缺失时停止并报出缺失字段，不硬猜。
3. **fuzzy_match_products** — 用 `--model` 匹配产品型号、条码、SKU编码、商品名称，命中 `--brand` 时先按品牌过滤；默认按猫超商品编码去重（`--by-sku` 退回 SKU 粒度），按匹配分数从高到低排序。
4. **collect_outputs** — 汇总结果；指定 `--output` 且非 dry-run 时写 JSON/Excel 并记录 Artifact。

## 字段识别（候选，命中第一个存在的表头）

- **商品上下架状态**：商品上下架状态 / 上下架状态 / 状态（必需）
- **猫超商品编码**：商品编码 / 猫超商品编码 / 商品ID / 平台商品ID / item_id（必需）
- **SKU编码**：SKU编码 / 平台SKUID / SKU ID / 平台SKU编码（必需）
- **条码**：条码 / 商品条码 / barcode（必需）
- **商品名称**：商品名称 / 标题 / 商品标题（必需）
- **品牌**：品牌 / 品牌名称 / 淘系品牌名称 / 自营品牌名称（可选）
- **产品型号**：产品型号 / 型号 / 规格型号 / 货品型号（可选）

> 当前主数据导出表里没有独立的「产品型号」列，型号信息主要落在「条码」与「商品名称」上，因此型号匹配会回退到这些字段；品牌取自「淘系品牌名称 / 自营品牌名称」。

## 输出结构

```json
{
  "query": { "brand": "...", "model": "..." },
  "dedupe_by": "product_code",
  "matched_count": 0,
  "results": [
    {
      "product_code": "...",
      "sku_code": "...",
      "barcode": "...",
      "brand": "...",
      "model": "...",
      "product_name": "...",
      "match_score": 0.95
    }
  ]
}
```

## dry-run 行为

- 仍会读取本地 Excel 并完成匹配（只读、安全），返回匹配预览。
- **不**写出 `--output` 文件（仅在 `output_skipped` 中提示）。
- 不触发任何平台请求。

## 边界

- 不修改原始 Excel；只读打开。
- 无任何平台 URL / Cookie / Token / Selector / Playwright / CDP。
- 必需字段无法识别时直接失败并列出缺失字段，绝不硬猜。
