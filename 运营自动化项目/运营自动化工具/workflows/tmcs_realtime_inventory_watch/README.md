# tmcs_realtime_inventory_watch workflow

猫超库存实时监测：复用既有平台下载能力，读取并合并 3 张表，输出存在库存风险的 SKU。

属"平台读取 + workflow 业务判断"类型：平台数据刷新全部复用 Ops-Cli 既有能力，本层只做
本地 Excel 读取、字段识别、合并、剩余库存与风险计算、产物与通知预览。

## 入口

```bash
python3 run.py workflow tmcs_realtime_inventory_watch --dry-run
python3 run.py workflow tmcs_realtime_inventory_watch --threshold 20 --dry-run
python3 run.py 猫超库存实时监测 --dry-run
python3 run.py 猫超库存实时监测 --threshold 20 --dry-run
```

## 参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `--brands` | 苏泊尔 奥克斯 | 聚水潭资料品牌筛选，支持空格或逗号分隔 |
| `--warehouse-code` | mc_aokesi_suolong | 猫超商家仓 code 筛选 |
| `--threshold` | 20 | 聚水潭实际库存低于此值进主表（库存风险） |
| `--tmcs-threshold` | 50 | 子表（猫超低库存）猫超可售低于此值才记录 |
| `--maochao-goods-file` | 主数据/猫超商品列表导出 (最新）.xlsx | 表3 本地文件 |
| `--use-local-jst-file` | 无 | **回放/测试用**：指定后跳过实时下载，直接读该聚水潭资料文件 |
| `--use-local-tmcs-stock-file` | 无 | **回放/测试用**：指定后跳过实时下载，直接读该猫超库存明细文件 |
| `--output` | 无 | 写出风险结果（按扩展名 .xlsx/.csv/.json，默认 xlsx），并记 Artifact |
| `--notify` | 否 | 有风险时才发送通知（dry-run 仍不发送） |
| `--execute` | 否 | **已废弃**：真实运行默认即实时下载，无需此开关（保留仅为兼容） |
| `--dry-run` | — | 只预览，绝不下载/发送（聚水潭用现有主数据预览、猫超跳过） |

## 实时性

本 workflow 用于**实时库存监测**，真实运行（非 dry-run）默认**每次都从平台实时下载**两份源数据，
不复用旧文件：

- 表1 聚水潭商品资料：实时经 `jst product sync` 从聚水潭后台导出最新资料（写主数据后读取）。
- 表2 猫超库存明细：实时经 `tmcs inventory export` 从猫超后台导出最新库存。

`--use-local-jst-file` / `--use-local-tmcs-stock-file` 仅供回放/测试时显式指定既有文件，
正式实时监测不应使用。dry-run 为安全预览：绝不下载，聚水潭用现有主数据、猫超跳过。

## 数据来源

| 表 | 来源 | 字段 |
|----|------|------|
| 表1 聚水潭商品资料 | 真实运行实时 `jst product sync` 下载；dry-run 读现有主数据 | 商品编码、实际库存、订单占有、品牌 |
| 表2 猫超库存明细 | 真实运行实时 `tmcs inventory export` 导出；dry-run 跳过 | 平台SKUID、专享/共享现货库存可售量、商家仓code |
| 表3 猫超商品列表 | 本地 Excel | SKU编码、条码、商品上下架状态 |

所有字段均支持候选字段名（见 `excel_loader.py`），识别不到时报清晰错误，不猜错列。

## 业务逻辑

1. 表3 筛「商品上下架状态=上架」，取 SKU编码、条码（用现有主数据，不自动刷新）。
2. 表1 筛品牌（苏泊尔/奥克斯），取实际库存、订单占有（剩余库存=实际−占有，仅作参考）。
3. 表4 中间表：表1.商品编码 = 表3.条码，输出 SKU编码 + 实际库存。
4. 表2 按仓 code 筛选，猫超可售库存 = 专享 + 共享现货可售量。
5. 记录口径：表4.SKU编码 = 表2.平台SKUID，**只看聚水潭实际库存 < threshold**；不要求猫超可售 > 实际（单边为 0 仍保留）；但**聚水潭实际库存=0 且 猫超可售=0** 两边都无货则剔除。

输出两张表（xlsx 两个 sheet；csv 另写 `_猫超低库存.csv`；json 两个键），字段一致
`SKU编码 / 商品名称 / 聚水潭实际库存 / 猫超实际库存(可售)`（商品名称取自表3 猫超商品列表）：

- **库存风险**（主表）：聚水潭实际库存 < threshold(20)，剔除两边都为 0。
- **猫超低库存**（子表）：排除主表后，聚水潭实际库存 ≥ threshold(20) 且 猫超可售 < tmcs_threshold(50)。

## 步骤与 dry-run 行为

| step | 作用 | dry-run / 默认行为 |
|------|------|--------------------|
| `check_inputs` | 校验 brands/warehouse_code/threshold/文件 | 只校验 |
| `refresh_jst_product_data` | 默认实时 `jst product sync` 下载 | dry-run 不下载，读现有主数据预览 |
| `refresh_tmcs_stock_data` | 默认实时 `tmcs inventory export` 导出 | dry-run 不下载，跳过 |
| `load_maochao_goods` | 读表3 | 只读本地 |
| `load_jst_products` | 读表1 | 只读本地 |
| `load_tmcs_stock` | 读表2 | 无文件则跳过 |
| `build_inventory_table` | 构建表4 | 纯计算 |
| `detect_inventory_risks` | 判风险 | 纯计算 |
| `write_outputs` | 写结果 + Artifact | 未指定 `--output` 不写文件 |
| `notify_if_needed` | 按需通知预览 | 无风险不通知；dry-run 不发送 |
| `collect_outputs` | 汇总 summary | 只汇总 |

summary 字段：`jst_rows / tmcs_stock_rows / active_tmcs_goods_rows / matched_rows /
low_remaining_stock_count / risk_count / risk_items`。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作全部经 `clients/ops_cli_client.run_ops_json`（复用 `jst product sync` 与 `tmcs inventory export`）。
- 不修改主数据 Excel，不重写已有聚水潭同步 / 猫超库存导出能力。
- 默认不触发真实下载；只有 `--execute` 且非 dry-run 才刷新平台数据。
