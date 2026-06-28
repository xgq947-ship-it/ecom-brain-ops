# 电商运营项目

这个仓库承载店铺运营自动化，分成两个固定层次：

- `Ops-Cli`：平台能力层。负责猫超、聚水潭、SessionHub、`9222` 专用浏览器、平台下载/查询、统一 JSON 输出。
- `运营自动化工具`：业务编排层。负责中文任务入口、workflow、Excel 处理、业务规则、归档、失败重试。

业务层只允许通过 `ops --json ...` 调平台，不自己直连平台。

## 项目结构

```text
02-运营店铺/
├── Ops-Cli/                  # 平台 CLI、SessionHub、平台能力文档
├── 运营自动化工具/             # 业务任务、workflow、业务文档
├── 主数据/                    # 聚水潭/猫超主数据
├── runtime/                  # 根目录运行产物
├── logs/                     # 根目录日志
├── docs/                     # 仓级文档补充
├── README.md
└── CLAUDE.md
```

## 核心能力

`Ops-Cli` 当前已覆盖：

- `tmcs`：商品同步、库存导出/调整、库存查询、月账单下载、推广账单下载、XP 工单数量读取、物流履约数据概览读取（已真实跑通，9222 + Playwright 读「日常考核」页）。
- `jst`：商品资料同步、订单打标、物流查询、揽收监控数据源、利润/统计、发票工单、店铺商品导入。
- `browser`：`9222` 浏览器检查。

`运营自动化工具` 当前已覆盖：

- 刷单表格登记、刷单订单插黄旗、刷单报销工单。
- 猫超月账单整理、猫超商品列表更新、猫超单品 ROI 测算、猫超 XP 工单监控、猫超物流履约监控、猫超资金表生成。
- 聚水潭商品资料更新、聚水潭揽收监控、聚水潭发票工单。
- 买家秀打包、公司网盘资料下载/索引、失败任务重放。

## 安装

平台层：

```bash
cd <项目根>/Ops-Cli
uv venv --python 3.11 .venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

业务层：

```bash
cd <项目根>/运营自动化工具
python3 -m pip install -r requirements.txt
```

## 常用命令

平台层：

```bash
cd <项目根>/Ops-Cli
./.venv/bin/ops --json browser check --port 9222
./.venv/bin/ops --json tmcs bill download --last-month
./.venv/bin/ops --json tmcs promotion-bill download --last-month
./.venv/bin/ops --json tmcs xp-workorder count
./.venv/bin/ops --json jst product sync
./.venv/bin/ops --json jst order pickup-watch --hours 48 --dry-run
```

业务层旧任务入口：

```bash
cd <项目根>/运营自动化工具
python3 run.py --list
python3 run.py 猫超账单整理
python3 run.py 更新猫超商品列表 --dry-run
python3 run.py 更新聚水潭资料 --dry-run
python3 run.py 猫超单品ROI测算 --sku-code AUXAMUZ8102R01 --dry-run
python3 run.py 猫超工单监控
python3 run.py 查看失败任务
```

业务层 workflow 入口：

```bash
cd <项目根>/运营自动化工具
python3 run.py workflow tmall_monthly_bill --dry-run
python3 run.py workflow tmcs_sku_roi --sku-code AUXAMUZ8102R01 --dry-run
python3 run.py workflow tmcs_xp_workorder_watch --dry-run
python3 run.py workflow append_brush_orders --dry-run
python3 run.py workflow retry_queue --dry-run
```

workflow 是旧任务的包装层：复用 `tasks/` 的成熟实现，把流程拆成 step，运行记录落到 `runtime/runs/`。详见 [运营自动化工具/docs/workflow_runtime.md](运营自动化工具/docs/workflow_runtime.md)。

## 关键配置

业务层主要配置文件：

- `运营自动化工具/config/paths.yaml`：本机路径配置。
- `运营自动化工具/config/pickup_watch.json`：揽收监控时效规则、班次、通知配置。
- `运营自动化工具/config/tmcs_sku_roi.json`：ROI 测算费率、利润率、理想推广占比。

平台层主要配置：

- `Ops-Cli/.env`：`SESSIONHUB_ROOT`、`PRIMARY_CHROME_CDP_URL` 等。
- `Ops-Cli/sessionhub/`：`9222` 浏览器与 scene 资产。

## 关键流程

猫超月账单链路：

1. `python3 run.py 猫超账单整理`
2. 业务层检查本地源文件是否存在
3. 缺失时调用 `Ops-Cli`
4. `Ops-Cli` 通过 SessionHub `9222` 专用浏览器下载
5. 业务层输出 `猫超{month}月账单数据表格.xlsx`

猫超 XP 工单监控链路：

1. `python3 run.py 猫超工单监控`
2. 业务层调用 `ops --json tmcs xp-workorder count`
3. `Ops-Cli` 直接读取猫超首页 DOM 文本中的 `XP工单处理 紧急(n)`
4. 业务层输出阈值判断结果；当前 `--notify` 仍是占位

猫超物流履约监控链路（已真实跑通）：

1. `python3 run.py 猫超履约监控`（或 `python3 run.py workflow tmcs_fulfillment_watch`）
2. 业务层调用 `ops --json tmcs fulfillment overview`
3. `Ops-Cli` 用 9222 + Playwright 进入猫超首页 → 商仓履约（天机）→ 物流履约 → 日常考核 → 数据概览，读取物流履约数据并统一 JSON 输出（含真实周预警等级 A/B/C；dry-run 返回 simulated 占位，不访问页面）
4. 业务层只负责考核/观测指标判断、周数据预警等级判断与通知预览；无风险时默认不输出通知，只记录运行结果
5. 该功能属于"平台读取 + workflow 业务判断"类型：平台读取放 `Ops-Cli`，指标与预警判断放 workflow，中文入口 `猫超履约监控` 放 `tasks/`，通知放 workflow notify step 并保证 dry-run 不发送

## 约束

- 平台操作统一沉到 `Ops-Cli`。
- 业务项目不复制 curl、不重写登录、不直接请求平台接口。
- 新增接口学习默认采用主浏览器探测 + `9222` SessionHub 执行的协作口径。
- 主浏览器负责真实页面观察；`9222` 只负责稳定执行、scene 沉淀和长期复用。

## AI Knowledge Base

本项目在以下位置维护了一份供 AI 读取的结构化知识库：

```
<ai知识库根>/
```

**用途：** 给 Hermes / Claude / GPT / Codex 读取项目能力、workflow、SOP 和决策记录。

**主更新入口（由当前 AI 会话理解并更新知识库）：**

```bash
cd <项目根>/运营自动化工具
python3 run.py workflow ai_knowledge_base_update \
  --source-root <项目根> \
  --kb-root <ai知识库根>
```

它会生成固定提示词、最新项目清单和目标文档清单；当前 AI 会话按这套请求更新知识库，再执行校验。

**legacy / fallback 索引刷新：**

```bash
cd <项目根>
python scripts/sync_ai_knowledge_base.py
```

**校验命令：**

```bash
python scripts/validate_ai_knowledge_base.py
```

**开发收尾规则：**

新增、修改或废弃 workflow 后，必须同步 AI Knowledge Base。最小收尾顺序：

```bash
cd <项目根>/运营自动化工具
python3 run.py workflow ai_knowledge_base_update \
  --source-root <项目根> \
  --kb-root <ai知识库根>

cd <项目根>
python3 scripts/validate_ai_knowledge_base.py
```

知识库入口以 `<ai知识库根>/AGENTS.md` 和 `KNOWLEDGE_INDEX.md` 为准。

**Hermes 优先读取顺序：**

1. `00-总览/知识库版本.md` — 版本、workflow 数量、校验状态
2. `00-总览/Hermes读取入口.md` — 任务决策树和读取路径
3. `00-总览/系统能力地图.md` — 全系统能力概览
4. `00-总览/自动化工作流总览.md` — 所有 workflow 执行命令
5. `00-总览/Workflow命令映射表.md` — 中文需求 → workflow 命令映射
6. `00-总览/当前项目状态.md` — 当前状态和最近变更
7. `01-工作流/<workflow_id>.md` — 具体 workflow 详情

---

## 更多说明

- [Ops-Cli/README.md](Ops-Cli/README.md)
- [运营自动化工具/README.md](运营自动化工具/README.md)
- [docs/文档同步检查报告.md](docs/文档同步检查报告.md)
