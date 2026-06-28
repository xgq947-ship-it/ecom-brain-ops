# tmcs_price_competitiveness_lookup — 猫超价格竞争力商品查询

按商品编码查询天猫超市「价格竞争力」列表里是否存在对应商品，输出「存在 / 不存在」。
支持**单个 / 批量**查询，整张列表**按天缓存**，批量与重复查询秒出结果。

## 命令

```bash
# 单个
python3 run.py workflow tmcs_price_competitiveness_lookup --product-code <商品编码>
python3 run.py 猫超价格竞争力查询 --product-code <商品编码>

# 批量（逗号分隔）
python3 run.py 猫超价格竞争力查询 --product-codes 1042043620771,1040897246648,999999999999

# 批量（文件，一行一个，# 开头为注释）
python3 run.py 猫超价格竞争力查询 --codes-file /path/to/codes.txt

# 取最新（强制重抓列表，忽略当天缓存）
python3 run.py 猫超价格竞争力查询 --product-code <商品编码> --refresh

# 预览
python3 run.py workflow tmcs_price_competitiveness_lookup --product-code TEST123 --dry-run
```

可选 `--screenshot-dir <目录>` 在真实重抓时截图存证。

## 缓存策略（按天）

- 整张「每日跟价商品」列表不大（数十条），一次性抓全缓存到本地 JSON。
- **当天首次**查询抓一次并写缓存；当天后续单个 / 批量查询**全部走缓存**（~0s）。
- **跨天自动重抓**（缓存文件按 `list_date` 命名），或显式 `--refresh` 强制重抓。
- 缓存目录：`runtime/cache/tmcs_price_competitiveness/list_<日期>.json`（`runtime/` 已 gitignore）。

## 步骤

1. `check_inputs`：合并 `--product-code` / `--product-codes` / `--codes-file`，去重保序；
   都为空报 `PRODUCT_CODE_REQUIRED`。
2. `load_list`：优先读当天缓存；缺失 / 跨天 / `--refresh` / dry-run 时调用
   `ops --json tmcs price-competitiveness list` 重抓并写缓存（dry-run / 模拟数据不写缓存）。
3. `match_codes`：对每个商品编码逐行精确匹配 `item_id`。
4. `collect_outputs`：单个输出「存在 / 不存在」，批量输出 found / missing 汇总。

## 分层边界

- 页面交互（进入价格竞争力页、设最大页大小、翻页读整张列表）在 Ops-Cli capability
  `tmcs price-competitiveness list`（9222 + Playwright，page DOM 模式，
  scene `tmall_chaoshi/price_competitiveness_lookup`）。
- 缓存读写、批量编码匹配在本 workflow 的纯业务模块
  [`cache.py`](cache.py)（不碰平台，只读写本地 JSON）。
- workflow / tasks **不出现** 任何猫超 URL、Cookie、Token、Selector、Playwright、CDP，
  只通过 `clients.ops_cli_client.run_ops_json` 消费 Ops-Cli 的单一 JSON 契约。
- 另保留 `tmcs price-competitiveness lookup`（页面按编码过滤的单条实时查询）capability，
  供需要绕过缓存的实时单查场景使用。

## 判定规则

- 商品编码 = 列表「商品信息」列里的 `ItemID`。
- 某行 `ItemID` **完全等于**查询编码 → 该编码 `exists=true`。
- 列表为空、或只有其它商品编码 → `exists=false`。逐行精确匹配，绝不只看「有没有数据」。

## dry-run 行为

- 向 Ops-Cli 透传 `--dry-run`，平台层返回 `simulated` 空列表，不访问真实猫超、不写缓存。
- 本 workflow 只读查询：不发通知、不写主数据、不下载文件、不改平台数据。
