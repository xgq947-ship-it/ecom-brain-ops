# 猫超店铺商品销售分析 workflow

`workflow_id`: `jst_tmcs_shop_product_sales_analysis`
中文入口：`猫超店铺商品销售分析`

下载指定月份或日期范围、指定门店的聚水潭「商品销售情况.csv」，调用集成的 CSV 分析脚本
（`csv_analyzer.py`，源自运营提供的 `maochao_ad_analyzer.py`，仅做最小适配），
输出店铺款式编码列表（优先推广 + 次级推广，去重保序）。

## 平台路径（仅供参考，实际下载在 Ops-Cli）

聚水潭 → 胜算 → 报表 → 商品利润 → 选择门店「（猫超）福安市启明工贸有限公司」→
选择月份（默认上个月）或日期范围 → 查询 → 点击导出数据 → 下拉框第一个「导出数据」→ 商品销售情况.csv

## 步骤

| step | 说明 | dry-run 行为 |
|---|---|---|
| `check_inputs` | 解析 `--month`/`--days`/`--start-date`/`--end-date`/`--shop-name`/`--use-local-file`/`--output`/`--dry-run`/`--execute`；默认上个月；`--days 7` 表示含今天在内近 7 天；未指定 `--output` 时默认输出到桌面 | 仅解析参数 |
| `fetch_sales_csv` | 有 `--use-local-file` 直接用本地 CSV；否则调 `ops --json jst report product-profit export`（下载在平台层） | 向 Ops-Cli 透传 dry-run（不带 `--execute`），平台返回 `simulated=true`，不真实导出；无本地文件时安全跳过 |
| `analyze_sales_csv` | 调 `csv_analyzer.analyze_sales_csv(csv_path)` 输出店铺款式编码 | 无 CSV 时跳过；缺「店铺款式编码」字段报清晰错误 |
| `write_outputs` | `--output` 指定时写 CSV/JSON/XLSX 结果 | dry-run 不落盘 |
| `collect_artifacts` | 记录原始 CSV、分析输出文件，落 `runtime/runs/` | 仅记录已存在文件 |

## 命令

```bash
# 本地 CSV，dry-run（不触平台）
python3 run.py workflow jst_tmcs_shop_product_sales_analysis --use-local-file /path/to/商品销售情况.csv --dry-run

# 指定月份，dry-run
python3 run.py workflow jst_tmcs_shop_product_sales_analysis --month 2026-05 --dry-run

# 近 7 天 / 近 30 天
python3 run.py workflow jst_tmcs_shop_product_sales_analysis --days 7 --execute
python3 run.py workflow jst_tmcs_shop_product_sales_analysis --days 30 --execute

# 指定日期范围
python3 run.py workflow jst_tmcs_shop_product_sales_analysis --start-date 2026-06-01 --end-date 2026-06-15 --execute

# 中文入口
python3 run.py 猫超店铺商品销售分析 --use-local-file /path/to/商品销售情况.csv --dry-run

# 真实导出 + 写出结果（需 Ops-Cli 已就绪）
python3 run.py workflow jst_tmcs_shop_product_sales_analysis --month 2026-05 --execute
```

## 输出与清理规则

- **不指定 `--output` 时默认生成 Excel 到桌面**：`<desktop_dir>/猫超店铺推广清单_<月份或日期范围>.xlsx`（分档位推广清单，`desktop_dir` 来自 `config/paths.yaml`）。
- **分析输出成功后，自动删除「我们下载的」原始 CSV**（`source=ops_export`）；
  `--use-local-file` 指定的你自己的本地文件**绝不删除**。删除记录在 `collect_artifacts.source_csv_deleted`，
  并在 Artifact 里留一条 `deleted_after_analysis` 溯源标记。
- dry-run 不写文件、不删除任何东西。

## 产物

- `sales_source`（csv）：下载/使用的原始「商品销售情况.csv」
- `style_code_output`（指定 `--output` 时）：输出格式由后缀决定
  - `--output xxx.xlsx` → **分档位推广清单 Excel**：🏆优先推广 / ✅次级推广 / ⚠️推广过高预警 / 🛑建议暂停，
    每行含 `店铺款式编码 / 商品名称 / 利润率 / 毛利率 / 销量 / 销售额 / 均价 / 退款率 / 当前推广 / 推广占比`
  - `--output xxx.csv` → 单列 `店铺款式编码`（优先+次级，去重保序）
  - `--output xxx.json` → 结构化结果（含 style_codes / categories / 计数）

## 边界

- CSV 下载只在 Ops-Cli（`ops --json jst report product-profit export`）。
- 本 workflow / tasks 不出现聚水潭 URL、Cookie、Token、Selector、Playwright、CDP。
- `csv_analyzer.py` 不含任何平台能力，纯 CSV 分析。
- 未提供分析脚本逻辑改写，仅做可 import / 结构化返回 / 去硬编码路径的最小适配。
