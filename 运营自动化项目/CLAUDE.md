# CLAUDE.md — ecommerce-store-ops 架构规范

> Claude Code / Codex 新增功能时必须严格遵守本文件所有规则。
> 详细 workflow runtime 说明见 `运营自动化工具/docs/workflow_runtime.md`。

---

## 1. 项目分层

```text
Ops-Cli/                        ← 平台能力层（唯一允许访问平台的代码）
运营自动化工具/                   ← 业务编排层（本项目主体）
  workflows/                    ← 业务流程层（step 化 workflow）
  tasks/                        ← 旧中文入口兼容层（仅保留旧入口）
  core/runtime/                 ← workflow 运行时内核（不含业务逻辑）
  clients/ops_cli_client.py     ← 业务层调用 Ops-Cli 的唯一桥接
```

各层职责：

| 层 | 职责 | 禁止 |
|---|---|---|
| Ops-Cli | 平台 API、浏览器、Session、Cookie、Token、Selector、Playwright、CDP | 业务 Excel、业务规则 |
| workflows/ | step 化业务编排、产物记录 | 平台 URL/Cookie/Token/Playwright/CDP |
| tasks/ | 旧中文命令兼容入口，可作为 workflow 的 wrapper | 承载新业务主逻辑 |
| core/runtime/ | WorkflowRunner、models、storage | 业务逻辑、平台逻辑 |
| clients/ops_cli_client.py | 唯一桥接，封装 `ops --json` 调用 | 直连平台、管理 session |

### 1.1 Python 运行环境（两层各自独立 venv）

| 层 | 解释器 | 说明 |
|---|---|---|
| 平台层 Ops-Cli | `Ops-Cli/.venv`（3.11，uv 管理，`requires-python>=3.11`） | Playwright 等需要 3.11 |
| 业务层 运营自动化工具 | `运营自动化工具/.venv`（3.11，uv 管理） | 装 `requirements.txt`：openpyxl / Pillow / requests |

- **业务层固定运行在它自己的 `.venv`**：`run.py` 顶部 `_maybe_reexec_in_business_venv()` 会在执行任何业务逻辑前自举——无论用系统 `python3`、Ops-Cli venv 还是别的解释器启动，都 re-exec 到 `运营自动化工具/.venv`，使「手动执行」与「Hermes 定时执行」收敛到同一解释器与依赖，杜绝 3.9 vs 3.11 分叉。文档里照常写 `python3 run.py` 即可。
- 重建业务 venv：`cd 运营自动化工具 && uv venv --python 3.11 .venv && uv pip install -r requirements.txt pytest`
- **不要让业务层去借用 Ops-Cli 的 venv**（缺业务依赖如 Pillow），也不要把业务依赖装进 Ops-Cli venv。

### 1.2 路径配置（零配置 / 锚点推导）

- 业务层所有路径由 `core/config_loader.py` 从**锚点推导**，不写死 `/Users/<name>/...`：
  - 仓库自身 / 电商Brain 目录树（主数据、刷单数据、01-产品库）→ 由 `PROJECT_ROOT` 推导
  - 系统目录（`~/Desktop`、`~/Downloads` 及其下文件）→ 由 `Path.home()` 推导
  - 微信目录（含 wxid）→ 自动 glob 探测
- 换电脑 / 别人 clone **默认零配置即可跑**；唯一可能要手填的是 NAS 挂载点（且仅 NAS workflow 需要）。
- 解析优先级（低→高）：推导默认 < `config/paths.yaml`（旧，gitignore）< `config/paths.local.yaml`（推荐，gitignore）< `OPS_PATH_*` 环境变量；非标准布局可用 `ECOM_STORE_ROOT` / `ECOM_BRAIN_DIR` 改锚点。
- 首次/换机执行 `python3 run.py setup`：体检全部路径 + 探测微信/NAS + 生成 `paths.local.yaml`。
- git 里只提交 `paths.yaml.example`（模板），**绝不提交 `paths.yaml` / `paths.local.yaml`**（含本机绝对路径 / wxid）。新增路径 key 时同步加进 `_derived_paths()` 推导，不要让消费方写死绝对路径。

---

## 2. 平台能力边界

**只能放在 Ops-Cli 的内容：**
- 猫超（天猫超市/tmcs）、聚水潭（jst/erp）、浏览器、SessionHub
- Cookie、Token、Authorization、LocalStorage、CSRF、x-token
- Selector、Playwright、CDP、平台 URL、真实 API 请求
- scene 学习、请求重放、登录恢复

**运营自动化工具（含 workflows/ 和 tasks/）绝对禁止：**
- 直接请求平台（禁止 requests/httpx 打平台 URL）
- `import sessionhub.*`
- 写 Cookie / Token / Session 管理逻辑
- 写 Playwright / CDP / 浏览器页面操作
- 写 Selector、平台 URL、登录等待逻辑
- 解析 stderr 的登录提示来决定业务流程
- 自行 fallback 到直连平台

业务层只消费 `ops --json` 的单一 stdout JSON 文档：`success / platform / command / data`，失败只读 `error_code / retryable / recovery_hint`。

---

## 3. 新增功能默认流程

### 新增平台能力
1. 让用户在主浏览器打开目标页面并完成复杂 UI 操作
2. 在 `Ops-Cli/sessionhub` 沉淀或更新 scene
3. 在 `Ops-Cli` 封装可复用平台命令（capability）

### 新增业务能力
4. 优先在 `workflows/` 新增 workflow（见第 4 节规范）
5. 旧中文命令通过 `tasks/<name>.yaml` 的 `alias` 映射到 workflow

### "平台读取 + workflow 业务判断"类功能
部分功能只读取平台数据，再由业务层判断指标/预警与通知，例如已落地的**猫超物流履约监控**（workflow_id `tmcs_fulfillment_watch`，中文入口 `猫超履约监控`）：
- 平台读取放 `Ops-Cli`：进入后台、天机/商家仓履约/日常考核页面跳转、读取「数据概览」走 `ops --json tmcs fulfillment overview`，只输出原始数值的统一 JSON。
- workflow 只负责考核指标判断、观测指标判断、周数据预警等级判断、通知预览。
- 中文入口放 `tasks/<name>.yaml`；通知放 workflow notify step，统一走 `send_notification`。
- 无风险默认不输出通知，只记录运行结果；dry-run 只预览，不发送通知、不处理平台数据。
- workflow / tasks 内禁止出现猫超 URL、Cookie、Token、Selector、Playwright、CDP，也不得把平台读取逻辑写进业务层。

### 禁止的做法
- 不新增散落的一次性脚本
- 不绕过 WorkflowRunner 直接执行
- 不在 `run.py` 或 `core/` 写业务逻辑
- 不重复在 tasks 层实现已有 workflow 的业务

---

## 4. Workflow 规范

### 目录结构（必须完整）

```text
workflows/<workflow_id>/
  __init__.py       ← 空文件，标记 Python 包
  workflow.py       ← 导出 build_workflow() -> Workflow
  steps.py          ← step handler，编排同包业务模块
  <domain>.py       ← 纯业务逻辑模块（如 risk_evaluator.py / roi_calculator.py），可选
  README.md         ← 步骤、dry-run 行为、产物、边界说明
```

### 每个 workflow 必须

- 支持 `--dry-run`（dry-run 下所有危险动作必须跳过，见第 7 节）
- step 拆分清晰，每个 step 独立写 `StepRun` 状态到磁盘
- 输出 `Artifact`（见第 6 节规范）
- 失败时返回 `failure_result(errors=...)` 给出清晰错误信息
- 危险写入必须有明确 `--execute` 或等价参数保护
- 业务主逻辑放 `workflows/<id>/` 内的纯业务模块（如 `risk_evaluator.py`），step handler 只做编排，不在 workflow 层重写已有逻辑
- 业务逻辑**不得沉在 `tasks/`**；`tasks/<name>` 只能是透传给 workflow 的薄 wrapper（见第 5 节）。当前已无 workflow `import tasks.*` 的残留债（2026-06 核查）；新增时仍须保持 workflow 不反向依赖 tasks 层，且别名不要用 `legacy` 误导成待迁移债

### step 失败语义

- `required=True`（默认）：失败中断整个 workflow，TaskRun 记 `failed`
- `required=False`：失败只记 `failed`，继续后续步骤

### 验证命令

```bash
python3 run.py workflow <id> --dry-run
python3 -m pytest -q
```

### AI Knowledge Base 同步

新增、修改或废弃 workflow 后，必须同步 `<ai知识库根>`。

最小收尾顺序：

```bash
cd <项目根>/运营自动化工具
python3 run.py workflow ai_knowledge_base_update \
  --source-root <项目根> \
  --kb-root <ai知识库根>

cd <项目根>
python3 scripts/validate_ai_knowledge_base.py
```

知识库 Agent 入口是 `<ai知识库根>/AGENTS.md`，统一索引是 `KNOWLEDGE_INDEX.md`。如果历史 memory 与知识库冲突，以知识库为准。

---

## 5. Tasks 规范

`tasks/` 只保留旧入口兼容，不再承载新增业务主逻辑。

- 可作为 wrapper 调用 workflow（`run.py workflow <id>`）
- 必须保留中文 alias 和旧命令习惯（不破坏现有触发词）
- 新增中文命令：在 `tasks/<name>.yaml` 声明 `name / aliases / fuzzy_keywords / entrypoint`
- `task.yaml` 自动注册，无需修改 `task_registry.py`
- tasks 平台调用统一走 `clients/ops_cli_client.py`
- tasks 不直接管理浏览器、Cookie、Token、Session

---

## 6. Artifact 规范

所有文件产物必须用 `core.runtime.Artifact` 记录，字段：

| 字段 | 说明 |
|---|---|
| `type` | 文件类型，如 `xlsx / csv / json` |
| `role` | 用途标识，如 `output / hdb_source / statement_list` |
| `name` | 文件名 |
| `path` | 完整绝对路径 |
| `platform` | 来源平台，如 `tmcs / jst` |
| `month` | 业务月份，如 `2026-05` |
| `metadata` | 扩展字段（dict） |

落盘位置：`runtime/runs/YYYY-MM/run_xxx/artifacts.json`

---

## 7. Dry-run 安全规范

`--dry-run` 模式下，以下操作**绝对禁止**执行：

| 禁止动作 | 原因 |
|---|---|
| 真实下载平台文件（猫超/聚水潭） | 会消耗平台配额、触发状态变更 |
| 真实上传/导入聚水潭 | 会污染 ERP 数据 |
| 真实写 Excel 主数据 | 会破坏生产文件 |
| 真实发送微信/企业微信通知 | 会骚扰用户 |
| 真实移动/删除 NAS 文件 | 不可逆 |
| 真实修改平台订单/商品/备注 | 不可逆 |

dry-run 应返回 `success_result(outputs={"skipped": True, "reason": "dry-run 跳过"})`，不得静默跳过不返回任何输出。

通知统一走 `core.runtime.send_notification(content, dry_run=ctx.dry_run)`，dry-run 自动不发送。

---

## 8. 测试规范

新增功能必须至少覆盖以下测试项：

1. **workflow 能注册**：`discover_workflow(<id>)` 不抛异常
2. **workflow dry-run 能跑通**：`python3 run.py workflow <id> --dry-run` 正常退出
3. **旧中文入口 dry-run 兼容**：对应中文命令 `--dry-run` 不报错
4. **Artifact 记录**：dry-run 产出 Artifact 结构正确（或明确记录 skipped）
5. **危险动作在 dry-run 不执行**：mock 危险函数，断言 dry-run 时未调用
6. **全量测试通过**：`python3 -m pytest -q` 零失败

测试文件放 `运营自动化工具/tests/`。

---

## 9. Git 规范

- 每个功能一个 commit，commit message 以功能类型开头（`feat/fix/refactor/docs`）
- 修改前先 `git status`，修改后必须 `git diff --stat` 和 `git status`
- **不提交以下内容**：
  - `runtime/runs/`（运行记录）
  - `logs/`（日志）
  - `**/cache/`、`**/.cache/`
  - `*session*`、`*cookie*`、`*token*`（含敏感登录态的文件）
  - `output/`（临时输出，除非明确需要版本化）
- 不把 Cookie / Token / Authorization 明文写入任何 `.md`、`.yaml`、`.json` 文档

---

## 10. 禁止事项（全局红线）

以下操作**无论何种理由都不允许**：

| 禁止项 | 说明 |
|---|---|
| 引入数据库（SQLite/PostgreSQL/MySQL 等） | 除非明确要求 |
| 引入 FastAPI / Flask / 前端框架 | 除非明确要求 |
| 引入 Celery / Redis / Docker / K8s | 除非明确要求 |
| 大规模移动旧代码目录 | 会破坏历史 import 路径 |
| 删除仍被 workflow import 复用的旧函数 | 会导致 workflow 报错 |
| 修改真实 session / cookie / token | 会破坏生产登录态 |
| 破坏 Ops-Cli JSON 输出契约 | 业务层依赖此契约 |
| 破坏旧中文命令（改名/删除 task.yaml alias） | 会影响现有使用习惯 |
| workflow 层写平台逻辑 | 违反架构分层 |
| 散落一次性脚本 | 不可追踪、不可复用 |
| 绕过 WorkflowRunner 直接执行业务 | 破坏可观测性 |

---

## 快速参考

```bash
# 查看所有任务
python3 运营自动化工具/run.py --list

# 运行旧中文任务（dry-run）
python3 运营自动化工具/run.py 猫超账单整理 --dry-run

# 运行 workflow（dry-run）
python3 运营自动化工具/run.py workflow tmall_monthly_bill --dry-run

# 全量测试
cd 运营自动化工具 && python3 -m pytest -q

# 查看最近运行记录
python3 运营自动化工具/run.py runs --limit 10
```

详细说明：
- Workflow runtime → `运营自动化工具/docs/workflow_runtime.md`
- 平台边界 → `运营自动化工具/docs/project_boundary.md`
- Ops-Cli 调用规范 → `运营自动化工具/docs/ops_cli_integration.md`
- 架构说明 → `运营自动化工具/docs/architecture.md`

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
