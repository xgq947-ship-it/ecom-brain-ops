# Ops-Cli

`Ops-Cli` 是两个项目里的唯一平台能力层。

- 负责：猫超、聚水潭、浏览器自动化、双浏览器学习、9222 SessionHub、Cookie/Session/LocalStorage、页面动作、上传下载、平台 API、统一 JSON 输出。
- 不负责：Excel 业务编排、刷单业务规则、买家秀编排、NAS 业务流程、自然语言任务分发。这些都留在 `运营自动化工具`。

## 当前支持的平台

- `tmcs`
  - 天猫超市 / 猫超
  - 商品列表同步
  - 库存导出 / 库存调整 / 一盘货库存查询
  - 月账单下载
  - 资金表数据读取：待收货款、推广账户余额、截图凭证
  - XP 工单数量读取（首页 DOM 提取）
  - 物流履约数据概览读取（已真实跑通，9222 + Playwright）：首页 → 商仓履约（天机）→ 物流履约 → 日常考核 → 数据概览
- `jst`
  - 聚水潭认证检查
  - 商品资料导出同步
  - 订单打标
  - 订单物流查询
  - 订单揽收监控数据源（复用订单列表与 `order logistics` 轨迹查询能力）
  - 订单统计 / 利润统计
  - 店铺商品导入
  - 发票工单能力
- `browser`
  - 9222 端口检查
  - 主浏览器学习辅助

## 架构边界

- `Ops-Cli` 只做平台能力，不做业务编排。
- 所有平台 URL、Cookie、Token、Selector、Requests、Playwright、CDP 连接、Scene 复检，都统一沉到这里。
- `运营自动化工具` 只能通过 `subprocess` 调用 `ops ...`，不能自己直接请求平台。

详细说明见：

- [架构设计](docs/architecture.md)
- [平台能力说明](docs/platform_capabilities.md)
- [双浏览器学习方案](docs/double_browser_learning.md)
- [9222 调试说明](docs/browser_9222_debug.md)
- [CLI 调用规范](docs/ops_cli_contract.md)

## 安装

```bash
cd <项目根>/Ops-Cli
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

或：

```bash
cd <项目根>/Ops-Cli
source .venv/bin/activate
pip install -e .
```

## 命令风格

统一只保留这种命名：

```bash
ops --json tmcs stock query
ops --json tmcs product sync
ops --json tmcs bill download
ops --json jst product sync
ops --json jst browser learn
ops --json jst shop-goods import
ops --json browser check
```

不再把 `python xxx.py`、`run_import.py`、`tmp_script.py`、`browser_test.py` 当成平台层正式接口。

## 常用命令

```bash
ops --help
ops --json browser check --port 9222

ops --json tmcs auth check
ops --json tmcs product sync
ops --json tmcs product sync --dry-run
ops --json tmcs product sync --use-local-only
ops --json tmcs product sync --force-refresh
ops --json tmcs bill download --last-month
ops --json tmcs bill download --download-statement-list --last-month
ops --json tmcs promotion-bill learn --source all
ops --json tmcs promotion-bill download --last-month
ops --json tmcs promotion-bill download --source zdx --last-month
ops --json tmcs promotion-bill download --source wxt --last-month
ops --json tmcs inventory export
ops --json tmcs stock query --item-ids 1052534376394,234567 --warehouse-code mc_aokesi_suolong --output json
ops --json tmcs xp-workorder count
ops --json tmcs xp-workorder count --threshold 4
ops --json tmcs xp-workorder count --dry-run
ops --json tmcs xp-workorder learn
ops --json tmcs fund receivable-bill sum --month 2026-05 --screenshot-dir ~/Desktop/猫超资金表_截图凭证
ops --json tmcs fund promotion-balance sum --screenshot-dir ~/Desktop/猫超资金表_截图凭证

ops --json jst auth check
ops --json jst product sync
ops --json jst product sync --dry-run
ops --json jst product sync --use-local-only
ops --json jst product sync --keep-brands 奥克斯 苏泊尔
ops --json jst order label --input /path/to/latest_brush_orders.json
ops --json jst order label --input /path/to/latest_brush_orders.json --limit 10
ops --json jst order label --input /path/to/latest_brush_orders.json --execute
ops --json jst order remark --order-id 123456 --remark-text "需要填写的卖家备注" --execute
ops --json jst order logistics --outer-order-id TB001 --outer-order-id TB002
ops --json jst order logistics --input /path/to/orders.txt --limit 10
ops --json jst order pickup-watch --hours 48 --dry-run
ops --json jst order reimburse --outer-order-id 3302371490526182153 --principal-total 965 --payout-total 140 --product-code SUZBHLYZHH1001 --workbook-file /path/to/register.xlsx
ops --json jst order reimburse --outer-order-id 3302371490526182153 --principal-total 965 --payout-total 140 --product-code SUZBHLYZHH1001 --workbook-file /path/to/register.xlsx --execute
ops --json jst browser learn --scene shop-goods-import
ops --json jst shop-goods import --file /path/to/jst_shop_goods_import.xlsx --shop-name "（猫超）启明工贸有限公司" --mode cover --output json
```

推广账单默认下载上一个自然月；智多星资金流水导出按页面文件中心返回完整 `.xlsx` 原始表，万象台按阿里妈妈页面真实返回保留 `.csv`，统一落到 `~/Downloads`，再由业务编排层按账期汇总。

猫超 XP 工单数量命令当前口径：

- `tmcs xp-workorder count` 真实执行时不再依赖历史 scene 回放。
- 命令会直接打开 `https://web.txcs.tmall.com/`，从首页可见文本中提取 `XP工单处理 紧急(n)`。
- `learn` 入口仍保留兼容，但当前只返回说明：这条能力已经改成首页 DOM 读取，无需额外学习 scene。
- `--dry-run` 只返回 `simulated=true` 占位结果，不访问页面。

猫超物流履约数据概览命令（已真实跑通）：

- 命令：`ops --json tmcs fulfillment overview`（`learn` 入口 `ops --json tmcs fulfillment learn` 仅返回读取口径说明）
- 真实读取：9222 + Playwright 导航 猫超首页 → 商仓履约（天机）→ 物流履约 → 日常考核 → 数据概览，读取渲染出的 BI iframe 文本（Playwright 可跨域读 iframe 文本），解析卡片数值与「考核表现」横幅给出的周预警等级 A/B/C。
- 输出按真实页面口径：`pickup_24h_rate / pickup_48h_rate / door_delivery_rate / next_day_delivery_rate / four_cp_rate / four_cp_rate_ex_remote / delivery_promise_rate / avg_pay_to_sign_hours / exception_feedback_required` + `weekly_warning_level`。其中表达签准率/支签时长不在日常考核默认卡片时为 `null`。
- 该能力只读数据，按统一 JSON 输出原始数值；考核/观测指标判断、周预警分级与通知，全部留在业务层 workflow，不在 `Ops-Cli`。
- `--dry-run` 不访问页面、不处理平台数据，返回 `simulated=true` 的占位指标。
- 解析不到数据（如未登录跳登录页/页面结构变化）时返回 `FULFILLMENT_OVERVIEW_NOT_FOUND` 或登录态恢复提示。
- 进入后台、页面跳转、读取数据概览属于平台能力，必须放 `Ops-Cli`；URL、Cookie、Token、Selector、Playwright、CDP 不得外泄到业务层或文档示例。

猫超账单下载当前正式链路：

- `statement_bill_list_for_supplier`：抓账单列表页真实 `GET /statementBill/v3/listForSupplier`
- `statement_bill_dynamic_list`：触发 `对账单列表` 导出任务
- `download_file_query`：查询下载中心文件
- `tmcs bill download --last-month` 会把查询窗口顺延 3 天，兼容平台跨月出账
- `tmcs bill download --download-statement-list --last-month` 会同时下载 `HDB*.xlsx` 和 `对账单列表.xlsx`
- 正式交互执行时，scene 失效会进入 SessionHub 恢复流程：拉起 `9222` 专用浏览器、等待手动登录、自动刷新页面并执行固定动作后继续跑

当前依赖 SessionHub scene 的其他正式交互 CLI 也统一遵循同一恢复口径：

- 先复检 scene；缺失或失效时自动拉起 `9222` 专用浏览器
- 你只需要手动登录一次
- 登录后脚本会按 scene 配置自动刷新页面，或自动点击 `查询 / 搜索 / 导出` 这类固定按钮
- 捕获到新的 scene 后继续原来的 `tmcs/jst` 下载、同步、统计流程，不再因为登录态缺失直接中断
- `--dry-run`、`auth check` 与无 TTY 执行不会拉起浏览器；无 TTY 失效时返回 `AUTH_REQUIRED`
- 如需覆盖默认 TTY 判断，使用全局参数 `--interactive-login` 或 `--no-interactive-login`

## 环境变量

复制 `.env.example` 为 `.env`。

```env
SESSIONHUB_ROOT=<项目根>/Ops-Cli/sessionhub
PRIMARY_CHROME_CDP_URL=
JST_ORDER_STATS_STORE=（猫超）福安市启明工贸有限公司（肖国清）
JST_PRODUCT_SOURCE_PATH=<项目根>/主数据/聚水潭商品资料（最新）.xlsx
JST_PRODUCT_KEEP_BRANDS=奥克斯,苏泊尔
TMCS_PRODUCT_IMPORT_PATH=~/Downloads/猫超商品列表导出.xlsx
TMCS_PRODUCT_LATEST_PATH=<项目根>/主数据/猫超商品列表导出 (最新）.xlsx
TMCS_BILL_DOWNLOAD_DIR=~/Downloads
```

说明：

- `SESSIONHUB_ROOT` 当前默认指向 `Ops-Cli/sessionhub`，会话资产与平台执行代码放在同一个项目边界内。
- `PRIMARY_CHROME_CDP_URL` 是可选的主浏览器接管入口；主浏览器指你本机日常使用的 Google Chrome，不能填 `9222`。
- `9222` 固定给 SessionHub 专用浏览器。

## 双浏览器学习机制

- 主浏览器：你本机日常使用的 Google Chrome，也就是平时人工登录、打开平台后台的那个浏览器。
- 主浏览器用途：由用户先手动打开目标页面，并完成登录、店铺切换、筛选、下拉框、弹窗、翻页等容易误判的 UI 操作；Codex 只观察当前页面、触发或等待关键动作、捕获真实请求并提取接口结构。
- 新增 `Ops-Cli` 接口能力时，默认先请用户打开目标页；如果需要点某个按钮，也优先请用户操作到位，再抓关键请求，避免 Codex 在主浏览器里长时间试错找路径。
- `9222` 浏览器：只给 SessionHub 长期沉淀 scene、复检和稳定执行。
- 禁止把“只用 9222 的单浏览器 capture”叫做双浏览器学习。
- 正式业务执行尽量走 `run.py -> Ops-Cli -> SessionHub 9222`；只有链路缺口学习时才临时用主浏览器补真实接口信息，再回灌给 `9222` scene。

补充：

- `tmcs xp-workorder count` 当前不是抓接口学习场景，而是首页 DOM 文本提取场景；不需要主浏览器学习，也不依赖 `PRIMARY_CHROME_CDP_URL`。

## 9222 机制

- 作用：稳定执行、长期复用、会话校验、scene 沉淀。
- 不作用：主浏览器日常探测。
- 推荐命令：

```bash
ops --json browser check --port 9222
ops --json browser tabs --port 9222
ops --json browser cleanup --port 9222 --dry-run
ops --json browser cleanup --port 9222
ops --json tmcs auth ensure
ops --json jst auth ensure
```

`browser cleanup` 只清理保守范围：Ops-Cli 标记的残留工作页、已知平台的重复 URL 标签页、额外的 `about:blank`。正常保留一个 `ops-cli:keepalive` 空白页，避免 9222 窗口因标签归零而关闭。

## 输出标准

所有正式 CLI 都优先支持 `--json`，返回统一结构：

```json
{
  "success": true,
  "platform": "tmcs",
  "command": "product sync",
  "data": {
    "capability_id": "tmcs.product.sync",
    "artifacts": [],
    "context_path": "runtime/context/...",
    "session_recovery": {
      "required": false,
      "interactive": false,
      "scenes_refreshed": [],
      "retry_count": 0
    }
  }
}
```

统一约定：

- JSON 时间戳：ISO 8601，本地时区
- 文件名时间戳：`YYYYMMDD-HHMMSS` 或 `YYYYMMDD_HHMMSS`
- 日志目录：`logs/`
- 平台模板与长期资产：`data/`
- 运行上下文：`runtime/context/`
- 截图：`screenshots/` 或任务自己的截图目录
- 业务导出：`output/` 或命令说明中声明的固定目录

## 聚水潭揽收监控

平台层入口：

```bash
./.venv/bin/ops --json jst order pickup-watch --hours 48 --dry-run
./.venv/bin/ops --json jst order pickup-watch --hours 48
```

`pickup-watch` 只负责获取付款订单与物流轨迹、按 `data/jst/pickup_watch_config.json` 的关键词识别是否已有揽收节点，并输出统一 JSON。付款时间修正、风险阈值、17:30 后订单处理、报表和微信提醒属于 `运营自动化工具`。

当前已提供可离线验收的模拟订单，覆盖已揽收、各风险时长、猫超付款偏移、17:30 后订单、仅有单号无轨迹、轨迹无揽收关键词等情形。真实执行复用 `order_list` 分页和现有 `jst order logistics` 查询链路：有快递单号时查询轨迹并识别揽收节点；没有快递单号时直接标记为未揽收。默认按 `JST_ORDER_STATS_STORE` 店铺查询。

聚水潭如要求“查询轨迹”短信授权，命令会中止并提示先完成授权，避免把授权失败误判为未揽收。如后续猫超平台提供真实付款时间，再接入 `maochao_real_pay_time` 替代当前业务层修正逻辑。

## 目录

```text
Ops-Cli/
  src/ops_cli/
    cli.py          # 入口，自动扫描 platforms/ 下 platform.py 注册命令
    cli_helpers.py  # 共享执行辅助（_execute, _get_json_flag）
    capabilities.py # 动态能力注册表（各平台通过 register_capabilities() 注册）
    config.py
    output.py
    logger.py
    integrations/
    platforms/
      jst/
        platform.py  # JST 命令注册入口，导出 register(app, capabilities)
        ...
      tmcs/
        platform.py  # TMCS 命令注册入口，导出 register(app, capabilities)
        ...
  data/
  logs/
  runtime/
  docs/
```

## 与自动化运营项目的关系

`运营自动化工具` 现在统一通过：

```bash
ops --json ...
```

来调用平台层。

例如：

- `更新聚水潭资料` -> `ops --json jst product sync`
- `更新猫超商品列表` -> `ops --json tmcs product sync`
- `刷单订单插黄旗` -> `ops --json jst order label`
- `刷单报销登记` -> `ops --json jst order reimburse`
- `猫超账单下载` -> `ops --json tmcs bill download`

## 当前状态

- 平台能力文档已经集中到 `Ops-Cli/docs/`
- 运营项目的 skill / README / 任务文档已经改成调用 `Ops-Cli`
- `sessionhub` 资产目录已迁移到 `Ops-Cli/sessionhub`
