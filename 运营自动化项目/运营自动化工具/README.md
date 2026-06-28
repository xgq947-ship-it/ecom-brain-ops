# 运营自动化工具

这个项目现在只负责业务编排。

- 负责：skill、工作流、Excel 加工、任务分发、日志汇总、上下文记录、失败重试、NAS/买家秀/刷单等业务规则。
- 不负责：平台 API、Cookie、Token、Playwright、SessionHub 内部调用、浏览器自动化、平台 URL、Selector。

平台能力统一下沉到：

[`<项目根>/Ops-Cli`](<项目根>/Ops-Cli)

## 当前原则

- `run.py` 仍是统一业务入口
- 保留原中文任务名和模糊触发
- 具体平台动作统一通过 `subprocess -> ops --json ...`
- 不再在业务任务里直接请求 JST / TMCS
- 新增或重构 workflow 时，复杂跨步骤状态优先用 `ctx.typed_state(...)`，平台返回值优先用 `run_ops_command(...)` 的 typed result
- 真实 `jst` / `tmcs` 平台调用在每个业务进程首次请求前，公共客户端会先以 `--interactive-login ... auth ensure` 做一次认证预检；手动执行和后台自动化统一生效
- 同一进程内同一平台只预检一次；`--dry-run` 与 `auth` 命令不触发前置预检。预检后业务请求再次返回 `AUTH_REQUIRED` 时，交互终端仍会以 `--interactive-login` 重试一次

## 统一架构

```text
运营自动化工具
  -> run.py
  ├─ <中文任务名> -> core/task_registry.py (扫描 tasks/ 下 task.yaml 动态加载)
  │                  -> tasks/* (每个任务目录含 task.yaml 声明文件)
  │                  -> clients/ops_cli_client.py -> subprocess -> Ops-Cli
  └─ workflow <id> -> core/runtime/registry.py (扫描 workflows/<id>/workflow.py)
                       -> core/runtime WorkflowRunner -> 复用 tasks/* 成熟函数
```

两条链路并存：旧中文任务名走 `tasks/`（不变）；新 `workflow` 子命令走 `workflows/`，把同一条业务流水线拆成 step 化流程，逐步落运行记录到 `runtime/runs/`。workflow 是包装层，复用 `tasks/` 现成实现，不重写业务逻辑。详见 [workflow runtime 说明](docs/workflow_runtime.md)。

## 当前任务

- `append_brush_orders`
- `tag_jst_brush_orders`
- `jst_brush_reimburse_workorder`
- `jst_order_invoice_workorder`
- `buyer_show`
- `company_nas_listing`
- `company_nas_index`
- `process_maochao_bills`
- `update_jst_products`
- `update_maochao_goods`
- `tmcs_sku_roi`
- `tmcs_sync_jst_shop_goods`
- `tmcs_xp_workorder_watch`
- `tmcs_fulfillment_watch`
- `tmcs_fund_table_generate`
- `jst_pickup_watch`
- `retry_queue`

## 当前 workflow

step 化流程，入口 `python3 run.py workflow <id>`：

- `append_brush_orders` — 刷单表格登记包装层；`--dry-run` 会只读扫描微信源表，不复制、不写入
- `buyer_show` — 买家秀打包包装层
- `company_nas_index` — 公司网盘索引包装层
- `company_nas_listing` — 公司网盘资料下载/上架包装层
- `demo` — 最小可运行示例
- `jst_brush_reimburse_workorder` — 聚水潭刷单报销工单包装层
- `jst_order_invoice_workorder` — 聚水潭发票工单包装层
- `jst_order_label` — 聚水潭刷单订单打标包装层
- `jst_pickup_watch` — 聚水潭揽收监控包装层
- `jst_product_sync` — 聚水潭商品资料更新包装层
- `retry_queue` — 失败任务查看/重放包装层
- `ai_knowledge_base_update` — AI知识库同步更新（固定提示词 + 最新项目扫描 + 校验入口）
- `tmall_monthly_bill` — 猫超月账单整理包装层
- `tmall_product_list` — 猫超商品列表更新包装层
- `tmcs_sku_roi` — 猫超单品 ROI 测算
- `tmcs_sync_jst_shop_goods` — 猫超商品信息同步聚水潭店铺商品资料
- `tmcs_xp_workorder_watch` — 猫超 XP 工单数量监控
- `tmcs_fulfillment_watch` — 猫超物流履约监控（已真实跑通）
- `tmcs_fund_table_generate` — 猫超资金表生成（新建 Excel 并插入截图凭证）

## 任务与 Ops-Cli 的对应关系

- `更新聚水潭资料` -> `ops --json jst product sync`
- `更新猫超商品列表` -> `ops --json tmcs product sync`
- `刷单订单插黄旗` -> `ops --json jst order label`
- `刷单报销登记` -> `ops --json jst order reimburse`
- `聚水潭发票工单` -> `ops --json jst order invoice`
- `猫超账单下载阶段` -> `ops --json tmcs bill download`
- `猫超账单整理` -> `ops --json tmcs bill download` + `ops --json tmcs promotion-bill download`
- `tmcs_sync_jst_shop_goods` skill -> `ops --json tmcs stock query` + `ops --json jst shop-goods import`
- `猫超单品ROI测算` -> 只读本地 Excel + `config/tmcs_sku_roi.json`
- `猫超工单监控` -> `ops --json tmcs xp-workorder count`
- `猫超履约监控` -> `ops --json tmcs fulfillment overview`
- `猫超资金表生成` -> `ops --json tmcs fund receivable-bill sum` + `ops --json tmcs fund promotion-balance sum`
- `聚水潭揽收监控` -> `ops --json jst order pickup-watch --hours 48`
- `AI知识库同步更新` -> 固定提示词 + 当前 AI 会话理解 + `scripts/validate_ai_knowledge_base.py`

## 常用命令

```bash
python3 -m pip install -r requirements.txt
python3 run.py --list
python3 run.py 更新聚水潭资料 --dry-run --use-local-only
python3 run.py 更新猫超商品列表 --dry-run --skip-auto-download
python3 run.py 刷单表格登记 --dry-run
python3 run.py 聚水潭商品信息同步猫超 --item-ids 1052305450766 --import-jst --import-mode cover
python3 run.py 猫超单品ROI测算 --sku-code AUXAMUZ8102R01 --dry-run
python3 run.py 猫超工单监控
python3 run.py 刷单订单插黄旗 --dry-run --limit 1
python3 run.py buyer_show --buyer-show-path "/绝对路径/买家秀" --model "AQA-12D-838" --dry-run
python3 run.py buyer_show --buyer-show-path "/绝对路径/买家秀" --model "AQA-12D-838" --reset-rotation
python3 run.py 查看失败任务
python3 run.py 查看失败任务 --all --dry-run
python3 run.py 更新公司网盘索引 --dry-run
python3 run.py 聚水潭揽收监控 --dry-run --notify
python3 run.py 聚水潭揽收监控 --notify
python3 run.py workflow tmall_monthly_bill --dry-run
python3 run.py workflow tmall_monthly_bill --month 2026-05 --dry-run
python3 run.py workflow tmcs_sku_roi --sku-code AUXAMUZ8102R01 --dry-run
python3 run.py workflow tmcs_xp_workorder_watch --dry-run
python3 run.py workflow tmcs_fulfillment_watch --dry-run
python3 run.py workflow tmcs_fulfillment_watch --warning-margin 2 --dry-run
python3 run.py workflow tmcs_fulfillment_watch --simulate-risk --dry-run
python3 run.py 猫超履约监控 --dry-run
python3 run.py 猫超履约监控 --notify --dry-run
python3 run.py workflow tmcs_fund_table_generate --month 2026-05 --dry-run
python3 run.py 猫超资金表生成 --month 2026-05 --reserve-balance 123.45 --bank-card-balance 678.90
python3 run.py workflow append_brush_orders --dry-run
python3 run.py workflow retry_queue --dry-run
python3 run.py workflow ai_knowledge_base_update --source-root <项目根> --kb-root <ai知识库根>
```

## 关键配置

```text
config/paths.yaml             # 本机路径与主数据位置
config/pickup_watch.json      # 揽收监控时效、仓库班次、通知配置
config/tmcs_sku_roi.json      # ROI 测算参数
runtime/context/              # 旧任务上下文
runtime/runs/                 # workflow 运行记录
logs/                         # 任务与 workflow 日志
```

`config/tmcs_sku_roi.json` 当前字段：

- `supply_price_factor`
- `vip_discount_rate`
- `general_fee_rate`
- `other_fee_rate`
- `storage_fee_rate`
- `tax_rate`
- `management_fee_rate`
- `refund_rate`
- `refund_flat_fee`
- `domestic_shipping_fee`
- `gift_cost`
- `safe_profit_rate`
- `ideal_promotion_ratio`

## 猫超工单监控

业务入口：

```bash
python3 run.py 猫超工单监控
python3 run.py workflow tmcs_xp_workorder_watch --threshold 4 --dry-run
```

说明：

- 真实执行调用 `ops --json tmcs xp-workorder count`
- `Ops-Cli` 当前直接读取猫超首页 DOM 文本中的 `XP工单处理 紧急(n)`
- `count > threshold` 才视为超阈值
- `--notify` 目前仍是占位，不会真正发送通知

## 猫超物流履约监控（已真实跑通）

业务入口：

```bash
python3 run.py 猫超履约监控                      # 真实读取 9222 日常考核页
python3 run.py 猫超履约监控 --dry-run
python3 run.py 猫超履约监控 --notify --dry-run
python3 run.py workflow tmcs_fulfillment_watch --dry-run
python3 run.py workflow tmcs_fulfillment_watch --notify --dry-run
```

功能定位：属于"平台读取 + workflow 业务判断"类型功能。

- 中文入口：`猫超履约监控`，声明在 `tasks/tmcs_fulfillment_watch.yaml`。
- workflow_id：`tmcs_fulfillment_watch`，实现在 `workflows/tmcs_fulfillment_watch/`。
- 平台读取放 `Ops-Cli`：9222 + Playwright 进入猫超后台、商仓履约（天机）、物流履约、日常考核、数据概览，读取物流履约数据，统一走 `ops --json tmcs fulfillment overview`。业务层不写猫超 URL、Cookie、Token、Selector、Playwright、CDP，也不把平台读取逻辑写进业务层。
- workflow 只负责：考核指标判断、观测指标判断、周数据预警等级透传、通知预览。
- 参数：`--warning-margin`（接近预警容差，默认 2）、`--notify`、`--simulate-risk`（仅 dry-run，本地风险样本预览预警）。
- 通知规则：指标即将触发预警时输出通知信息；无风险时默认不发送通知，只记录运行结果。
- dry-run 只预览通知内容，不真实发送、不处理平台数据（平台层返回 `simulated=true`）。
- 真实模式：Ops-Cli 直接读「日常考核」页并返回真实指标 + 周预警等级（A/B/C）；解析不到时返回 `FULFILLMENT_OVERVIEW_NOT_FOUND`。
- 通知统一走 `core.runtime.send_notification(content, dry_run=ctx.dry_run)`，dry-run 保证不发送。

考核指标（要求达标，按真实日常考核页口径）：

- 24H 支揽率（T+2）：≥ 95%
- 48H 支揽率（T+3）：= 100%
- 送货上门率：≥ 75%（强上门心智仓考核；4CP 占比 ≥ 90% 关仓时可开白）
- 隔日达率：≥ 隔日达率商家底线 55%（非强上门心智仓考核）
- 预警只关注三个核心指标：24H支揽率、送货上门率、隔日达率

观测 / 记录指标（默认只记录，不自动预警）：

- 4CP 占比 / 4CP 占比_剔偏远（真实页面为 4CP，非 7CP）
- 支签时长（小时）（不在日常考核默认卡片时为 null）

其它：

- 履约异常单反馈：异常单据 > 0 即标记需反馈（severity=action）
- 周数据预警等级：A / B / C，来自「考核表现」横幅（severity=weekly）

周数据预警等级：

- A 类预警：已在整改期，下月可能被关仓风险；整改期内月度数据仍有不合格
- B 类预警：下月可能进入整改期风险；非整改期，当前月度数据有不合格
- C 类预警：未进入正式考核期，开仓未满 1 个月；月度数据有不合格

## 猫超单品 ROI 测算

入口：

```bash
python3 run.py 猫超单品ROI测算 --sku-code AUXAMUZ8102R01 --dry-run
python3 run.py workflow tmcs_sku_roi --product-code 762065566026 --dry-run
python3 run.py workflow tmcs_sku_roi --sku-code AUXAMUZ8102R01 --output "~/Desktop/roi_result.xlsx"
```

说明：

- 只读取本地猫超商品主表、聚水潭商品主表和 `config/tmcs_sku_roi.json`
- 不请求猫超后台，不请求聚水潭后台
- 输出口径为 `保本ROI`、`安全ROI`、`理想ROI`
- `--output` 支持 `.json` 和 `.xlsx`

## 聚水潭订单揽收监控

业务入口读取 `Ops-Cli` 返回的近 48 小时订单 JSON；只有存在异常订单时，才通过 `send_wecom` 发送简短的异常订单号清单，并标注距有效付款时间多久、已超时多久。猫超订单统一基于 `effective_pay_time` 计算风险：优先使用猫超真实付款时间，否则使用聚水潭付款时间减去配置偏移；当天 `17:30` 后付款的订单当晚抑制提醒。

```text
配置：config/pickup_watch.json
日志：logs/jst_pickup_watch_YYYYMMDD_HHMMSS.log
```

`--dry-run` 不请求真实聚水潭、不发送微信；模拟数据存在异常时输出模拟推送内容。正式执行无异常时仅记日志、不发送微信；有异常时通过本机 `~/.hermes/scripts/send_wecom.py` 推送，仅包含异常订单号和耗时信息，不生成异常订单 Excel/CSV。

定时启动器不存放在本项目，统一由 `~/Automation` 下的 launchd 启动器管理。

## 买家秀说明

- `buyer_show` 默认按 `买家秀路径 + 型号 + batch` 维护分组轮询状态，状态文件在 `runtime/buyer_show_rotation_state.json`
- 如果素材目录存在分组文件夹，默认只按分组轮询，不再退回到全目录硬切图
- 每个订单文件夹默认最多取 5 张图，但分组只要大于 3 张即可执行；1-3 张仍视为图片不足
- 同型号命中多个日期时，会按日期拆分批次并分别生成 zip
- 只有当素材目录完全没有分组文件夹时，才允许退回散图模式
- `--dry-run` 会输出日期批次、将使用的分组、轮询游标前后位置，以及是否因为分组/图片不足而不能执行
- `--groups` 会跳过轮询状态，严格按显式分组执行

## Skill 约束

- skill 只能调业务入口或 `Ops-Cli`
- skill 不能写死 URL / Cookie / Selector / Token
- skill 不能直接 import `sessionhub/*`
- skill 需要同步 README、`SKILL.md`、`skill.yaml`

详见：

- [架构说明](docs/architecture.md)
- [workflow runtime 说明](docs/workflow_runtime.md)
- [项目边界说明](docs/project_boundary.md)
- [Skill 开发规范](docs/skill_development_spec.md)
- [Ops-Cli 调用规范](docs/ops_cli_integration.md)
- [NotchFlow workflow 状态显示（可选）](docs/notchflow.md)
- [迁移报告](docs/migration_report.md)

## 目录

```text
运营自动化工具/
  clients/
    ops_cli_client.py
  config/
    paths.yaml
    paths.yaml.example
  core/
    task_registry.py    # 旧任务发现（tasks/*/task.yaml）
    runtime/            # workflow runtime 内核（models/result/storage/workflow/runner/registry）
  tasks/                # 旧脚本任务（不变）
  workflows/            # 新 step 化 workflow（包装层）
    append_brush_orders/
    buyer_show/
    company_nas_index/
    company_nas_listing/
    demo/
    jst_brush_reimburse_workorder/
    jst_order_invoice_workorder/
    jst_order_label/
    jst_pickup_watch/
    jst_product_sync/
    retry_queue/
    tmall_monthly_bill/
    tmall_product_list/
    tmcs_sku_roi/
    tmcs_sync_jst_shop_goods/
    tmcs_xp_workorder_watch/
    tmcs_fulfillment_watch/
  skills/
  logs/
  runtime/
    context/            # 旧任务运行上下文
    runs/               # workflow 运行记录（YYYY-MM/run_xxx/）
  docs/
  run.py
```

## 目录边界

- `sessionhub/` 已迁移到 `Ops-Cli/sessionhub`
- 本项目不再保存 SessionHub 代码或会话资产
- 旧平台实现文档已经迁移到 `Ops-Cli/docs/`
