# tmcs_sku_roi workflow

猫超单品 ROI 测算 workflow。只读本地 Excel，不请求猫超后台，不请求聚水潭后台，不修改主数据，也不依赖 ROI 模板 Excel。

## 入口

```bash
python3 run.py workflow tmcs_sku_roi --sku-code AUXAMUZ8102R01 --dry-run
python3 run.py workflow tmcs_sku_roi --product-code 762065566026 --dry-run
python3 run.py 猫超单品ROI测算 --sku-code AUXAMUZ8102R01 --dry-run
python3 run.py workflow tmcs_sku_roi --sku-code AUXAMUZ8102R01 --output "~/Desktop/roi_result.xlsx"
```

## 数据链路

1. 用 `--sku-code` 匹配猫超商品列表中的 `SKU编码`，或用 `--product-code` 匹配猫超商品列表中的 `商品编码`
2. 读取命中行 `条码`
3. 若 `--product-code` 命中多条不同条码，默认取第一条继续
4. 用 `条码 = 聚水潭商品资料.商品编码` 精确匹配聚水潭商品
5. 读取 `淘系控价` 和 `成本价`
6. 读取 `config/tmcs_sku_roi.json` 作为 ROI 默认业务参数
7. 用 Python 复刻保本 ROI / 安全 ROI，并增加推广占比 12% 的理想 ROI

## 输出

终端与 workflow outputs 仅输出：

- `保本ROI`
- `安全ROI`
- `理想ROI`

如指定 `--output`，支持写 `.json` 或 `.xlsx`，并记录为 `Artifact(role=output)`。

## dry-run

- 允许读取两份主数据 Excel 和一份 ROI 配置 JSON
- 允许做完整查询和计算
- 不写任何输出文件

## 公式口径

- 默认参数直接来自 `config/tmcs_sku_roi.json`
- 保本 ROI：按当前 Python 公式口径
- 安全 ROI：按目标利润率 `10%`
- 理想 ROI：新增口径，按 `推广费用 = 成交价 * 12%`

## 配置

默认读取：`运营自动化工具/config/tmcs_sku_roi.json`

后续如需调整：

- 供货价系数
- 通用收费率
- 其他收费率
- 税点
- 管理费用率
- 退款率
- 目标保留利润率
- 理想推广占比

直接修改这个配置文件即可，无需改代码。

## 风险控制

- `SKU编码` 找不到：直接报错
- `商品编码` 找不到：直接报错
- `条码` 为空：直接报错
- 聚水潭 `商品编码` 找不到或重复：直接报错
- `淘系控价` 若不是单值数字：直接报错，不猜
