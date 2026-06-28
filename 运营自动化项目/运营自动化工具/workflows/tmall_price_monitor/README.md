# tmall_price_monitor — 天猫商品价格监控

输入天猫商品ID或完整天猫商品链接，workflow **自动**通过猫超商品列表 + 聚水潭商品资料匹配出「淘系控价」，
再抓天猫页面实时价格做控价对比。不再需要手动输入控价。

## 完整链路

```
天猫商品ID / 完整商品链接
  → 【猫超商品列表】按商品ID匹配出【条码】
  → 用【条码】匹配【聚水潭商品资料】的【商品编码】
  → 读取该聚水潭行的【淘系控价】= control_price
  → 抓天猫商品实时价格（Ops-Cli）
  → 对比：实时价 < 淘系控价 → 低于控价；≥ → 正常
```

> 一个商品ID 可能对应多个条码（多 SKU），控价各不相同。为「低于控价」少误报，
> 取所有有效控价中的**最小值**作为代表控价（页面价低于最低控价才判定违规），
> 其余控价记录在 JSON 的 `all_control_prices`。

## 架构边界

- **天猫抓价**全部在 Ops-Cli 平台层（`ops tmall price get`，复用 9222/SessionHub 登录态）。
- **控价匹配**是纯本地 Excel/CSV 读取，放业务层 `control_price_mapper.py`，不接触任何平台。
- workflow 只消费 `ops --json` 实时价 + 本地控价做对比，无 URL/Cookie/Selector/Playwright/CDP。

## 实时价来源

到手价必须依赖 9222 Chrome 的淘宝买家端登录态。Ops-Cli 抓价顺序：

1. 纯商品ID会先在 Ops-Cli 平台层查询猫超商品列表 `searchItem`，读取该行的 `mid`，自动补成带 `mi_id` 的天猫详情链接。
2. 输入本身就是完整商品链接时，原样保留 `mi_id` 等营销参数。
3. 没有完整链接时，打开 H5 详情页并捕获 `mtop.taobao.detail.data.get` 响应。
4. 从结构化响应里优先解析 `data.apiStack[].value(JSON).price.price.priceText`、`priceModule.*PriceText`、`skuCore.sku2info.*.price.priceText` 等到手价字段。
5. mtop 无价格、返回 `redirectToV3` 或被 `RGV587` 风控拦截时，回退到 PC 详情页 DOM。
6. PC DOM 到手价（路 A 计算法，按优先级）：
   1. **主价块算到手价**：取「字号最大、含 ¥金额、文本≤30字」的可见价格块，按
      `到手价 = 参考价 − 直降 − 立减` 计算（`parse_deal_price_from_block`）。
      例：`活动价￥588.81` → 588.81；`超市推荐￥624起直降126元` → 624−126 = **498**。
   2. 带「领券后 / 加补后 / 到手价」标签的文案价（标签后必须紧跟 ¥，禁止跨数字误抓）。
   3. 老选择器；4. 页面文本第一个 ¥金额（最不稳兜底）。

> 到手价**不在详情页结构化数据里**（实测 SSR `__ICE_APP_CONTEXT__` 只有「超市推荐价 + 直降文案」，
> 没有现成到手价字段），因此用「参考价 − 直降/立减」推算。`满X减Y / 叠券` 这类条件叠加目前
> 不参与计算，可能有误差（控价对比按推算到手价判定）。

`mi_id` 这类活动上下文不能由商品ID稳定计算出来，必须来自猫超商品列表/API 的 `mid`、平台活动入口或完整商品链接。
如果自动补全失败且只抓到天猫超市裸价/普通价，Ops-Cli 会返回 `capture_status=price_context_missing`，
workflow 状态显示为「价格上下文缺失」，不会把裸价写成“正常”。

> 登录态现实：活动详情页（`chaoshi.detail.tmall.com` 的 PC SSR）风控较严，**9222 即便登录也可能被
> login-jump 弹回登录**（实测主浏览器的可信会话能稳定打开、9222 时灵时不灵）。当前策略是「养 9222」：
> 保持登录、别清缓存、低频跑，让淘宝逐渐把它当可信设备；过不了时状态记为「登录/验证码异常」。

如果本次抓价结果里出现 `capture_status=login_required` 或 `capture_status=captcha`，workflow 只在运行记录和报表里记录「登录/验证码异常」，当前策略不发送任何通知渠道。

## 命令

```bash
# 单个商品ID（自动查控价；Ops-Cli 会先尝试补全 mi_id 活动链接）
python3 run.py workflow tmall_price_monitor --item-id 1006136614102

# 完整活动链接（保留 mi_id 等营销参数）
python3 run.py workflow tmall_price_monitor --item-id "https://detail.tmall.com/item.htm?id=1052534376394&mi_id=..."

# 批量商品ID
python3 run.py workflow tmall_price_monitor --item-ids 762065566026,1006136614102

# 文件输入（CSV 只需 item_id 列）
python3 run.py workflow tmall_price_monitor --file config/tmall_price_items.csv

# 预览（不访问天猫，返回模拟价格；控价仍真实匹配）
python3 run.py workflow tmall_price_monitor --item-id 1006136614102 --dry-run

# 指定控价源文件（排查/覆盖自动查找）
python3 run.py workflow tmall_price_monitor --item-id 1006136614102 \
  --maochao-file "/path/猫超商品列表导出.xlsx" --jst-file "/path/聚水潭商品资料.xlsx"

# 中文入口
python3 run.py 天猫商品价格监控 --item-ids 762065566026,1006136614102
```

`config/tmall_price_items.csv` 现在只需要：

```csv
item_id
762065566026
1006136614102
```

参数：

| 参数 | 说明 |
|---|---|
| `--item-id` | 单个天猫商品ID或完整商品链接 |
| `--item-ids` | 逗号分隔的天猫商品ID/完整商品链接批量 |
| `--file` | 输入 CSV（只需 `item_id` 列，可填商品ID或完整链接；旧的 `item_id,control_price` 也兼容，控价列被忽略） |
| `--maochao-file` | 指定猫超商品列表文件（默认自动查找） |
| `--jst-file` | 指定聚水潭商品资料文件（默认自动查找） |
| `--output-dir` | 输出目录（默认 `outputs/tmall_price_monitor/`） |
| `--dry-run` | 预览，不访问天猫、返回模拟实时价（控价仍真实匹配） |

## 依赖文件（控价来源）

自动查找顺序：项目配置锚点（存在即用）→ 按关键词在候选目录找最新（xlsx/xls/csv）。

| 文件 | 默认位置（锚点） | 文件名关键词 |
|---|---|---|
| 猫超商品列表 | `主数据/猫超商品列表导出 (最新）.xlsx` | 猫超商品列表 / 天猫超市商品列表 / 商品列表导出 |
| 聚水潭商品资料 | `主数据/聚水潭商品资料（最新）.xlsx` | 聚水潭商品资料 / 商品资料 / JST商品资料 |

候选查找目录：`主数据/`、`~/Downloads/`、`~/Desktop/`、业务层 `data/ input/ downloads/`。

字段映射（自动识别表头，命中第一个存在的）：
- 猫超商品列表：商品ID 列候选 `商品ID/item_id/id/商品编码/货品编码/SKU编码`；条码列候选 `条码/商品条码/barcode`
- 聚水潭商品资料：`商品编码`（= 猫超条码）、`淘系控价`、`商品名称`

读取时所有单元格按字符串清洗，长数字不被科学计数法破坏；商品ID/条码去结尾 `.0`、保留前导 0；
控价去 `¥￥元 逗号空格` 并提取首个有效数字，空值记为 None。

## 步骤

| step | 说明 |
|---|---|
| `check_inputs` | 解析商品ID输入（单个/批量/CSV） |
| `resolve_control_prices` | 读猫超+聚水潭，匹配淘系控价（输出文件路径、各商品匹配状态、控价） |
| `fetch_realtime_prices` | 只对匹配到控价的商品调 `ops tmall price get` 抓实时价 |
| `compare_prices` | 控价对比、差价、状态判定 |
| `notify_login_if_needed` | 发现登录失效/滑块验证时只记录异常商品，不发送通知 |
| `write_outputs` | 写出 Excel 与 JSON，记录 Artifact |
| `collect_outputs` | 汇总最终结果 |

`resolve_control_prices` 会向 stderr 打印调试信息：猫超/聚水潭文件路径、各商品ID的条码匹配
结果与控价读取结果。

## 状态规则

| 状态 | 触发条件 |
|---|---|
| 低于控价 | 实时价 < 淘系控价（`diff = 实时价 - 控价`，为负） |
| 正常 | 实时价 ≥ 淘系控价 |
| 未找到猫超条码 | 猫超商品列表没有匹配到该商品ID |
| 未找到聚水潭商品 | 条码无法匹配聚水潭商品编码 |
| 控价为空 | 淘系控价为空或无法转数字 |
| 抓取失败 | 控价匹配成功，但天猫实时价抓取失败 |
| 价格上下文缺失 | 只输入商品ID时抓到天猫超市裸价/普通价，可能缺少 `mi_id` 活动上下文 |
| 价格为空 / 商品不存在 / 登录·验证码异常 | 抓价阶段对应的细分失败（页面无价 / 已下架 / 登录页·滑块） |

> 匹配未成功的商品不会去抓天猫（节省请求、避免无谓登录摩擦）；批量下单个失败不影响其它。

## 产物

输出目录：`outputs/tmall_price_monitor/`

- `天猫商品价格监控_YYYYMMDD_HHMMSS.xlsx`
  字段：商品ID / 商品标题名称 / 条码 / 聚水潭商品编码 / 聚水潭商品名称 / 淘系控价 /
  商品实时价格 / 商品差价 / 状态 / 抓取时间 / 截图路径（「低于控价」行高亮）
- `天猫商品价格监控_YYYYMMDD_HHMMSS.json`
  含 `summary`、`below_control`（字段含 barcode/jst_goods_code/jst_goods_name/taoxi_control_price/diff_price）、`items`
- `screenshots/`：每个被抓价商品一张存证截图

## 常见失败原因

| 现象 | 原因 / 处理 |
|---|---|
| 全部「未找到猫超条码」 | 商品ID不在猫超商品列表，或列表文件过旧 → 先更新「猫超商品列表」 |
| 全部「未找到聚水潭商品」 | 条码与聚水潭商品编码对不上，或聚水潭资料过旧 → 更新「聚水潭商品资料」 |
| 「控价为空」 | 聚水潭该商品「淘系控价」没填 → 在聚水潭补控价 |
| 「登录/验证码异常」 | 9222 浏览器未登录淘宝/天猫或命中滑块 → 在 9222 窗口扫码登录/处理验证后重跑；当前不发送通知 |
| 抓到 `￥699` 这类标价，与手机实际价差很大 | 猫超商品列表未返回有效 `mid`、活动上下文失效，或完整链接缺少 `mi_id` → 换完整活动链接重跑 |
| 「价格上下文缺失」 | 自动补全 `mi_id` 失败且只抓到裸价/普通价，workflow 已阻断裸价误报 → 输入完整商品链接重跑 |
| 「未找到…文件」 | 把猫超列表/聚水潭资料放到 主数据/ 或 ~/Downloads/，或用 `--maochao-file/--jst-file` 指定 |
| `.xls` 读不了 | 项目未装 xlrd → 用 Excel 另存为 `.xlsx` |

## 测试

- 平台层：`Ops-Cli/tests/test_tmall_item_price.py`
- 业务层：`运营自动化工具/tests/test_tmall_price_monitor_workflow.py`
