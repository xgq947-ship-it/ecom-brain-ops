# AGENTS.md — 运营自动化工具架构规范

> Codex / Claude Code 新增功能时必须严格遵守本文件所有规则。
> 本文件与根目录 `CLAUDE.md` 保持一致，是 `运营自动化工具/` 子目录的权威规范。

---

## 项目定位

这是 `<项目根>/运营自动化工具` 的长期本地电商运营自动化项目。默认环境是 macOS + Zsh，业务围绕天猫超市、聚水潭 ERP、公司 NAS、Excel/WPS 表格和本地文件流。

所有长期任务必须服务于"统一入口、可复用登录态、可追踪运行记录、可安全回放"的架构，不要把一次性脚本散落到项目外。

---

## 1. 项目分层

```text
Ops-Cli/                        ← 平台能力层（唯一允许访问平台的代码）
运营自动化工具/                   ← 业务编排层（本目录）
  workflows/                    ← 业务流程层（step 化 workflow）
  tasks/                        ← 旧中文入口兼容层（仅保留旧入口）
  core/runtime/                 ← workflow 运行时内核（不含业务逻辑）
  clients/ops_cli_client.py     ← 业务层调用 Ops-Cli 的唯一桥接
```

调用关系：

```text
run.py
  ├─ <中文任务名> → core/task_registry.py → tasks/*/main.py          （旧链路，不变）
  └─ workflow <id> → core/runtime/registry.py → workflows/<id>/workflow.py
                       → core/runtime WorkflowRunner → 复用 tasks/* 成熟函数
```

各层职责：

| 层 | 职责 | 禁止 |
|---|---|---|
| Ops-Cli | 平台 API、浏览器、Session、Cookie、Token、Selector、Playwright、CDP | 业务 Excel、业务规则 |
| workflows/ | step 化业务编排、产物记录 | 平台 URL/Cookie/Token/Playwright/CDP |
| tasks/ | 旧中文命令兼容入口，可作为 workflow wrapper | 承载新业务主逻辑 |
| core/runtime/ | WorkflowRunner、models、storage | 业务逻辑、平台逻辑 |
| clients/ops_cli_client.py | 唯一桥接，封装 `ops --json` 调用 | 直连平台、管理 session |

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

### 新增平台能力（先做这步）
1. 让用户在主浏览器打开目标页面并完成复杂 UI 操作
2. 在 `Ops-Cli/sessionhub` 沉淀或更新 scene
3. 在 `Ops-Cli` 封装可复用平台命令（capability）

### 新增业务能力
4. 优先在 `workflows/` 新增 workflow（见第 4 节规范）
5. 旧中文命令通过 `tasks/<name>.yaml` 的 `alias` 映射到 workflow

### "平台读取 + workflow 业务判断"类功能

部分功能只读取平台数据，再由业务层做指标/预警判断与通知，例如已落地的**猫超物流履约监控**（workflow_id `tmcs_fulfillment_watch`，中文入口 `猫超履约监控`）。这类功能严格按下面分工落地：

- 平台读取放 `Ops-Cli`：进入后台、天机/商家仓履约/日常考核页面跳转、读取「数据概览」走 `ops --json tmcs fulfillment overview`，只输出原始数值的统一 JSON。
- 业务判断放 workflow：考核指标判断、观测指标判断、周数据预警等级判断、通知预览，全部在 `workflows/<id>/steps.py`。
- 中文入口放 `tasks/<name>.yaml`（声明 `name / aliases / fuzzy_keywords / entrypoint`）。
- 通知放 workflow 的 notify step，统一走 `core.runtime.send_notification(content, dry_run=ctx.dry_run)`。
- 无风险默认不输出通知，只记录运行结果；dry-run 只预览通知内容，不真实发送、不处理平台数据。
- workflow / tasks 内**禁止**出现猫超 URL、Cookie、Token、Selector、Playwright、CDP，也不得把平台读取逻辑写进业务层。

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
  steps.py          ← step handler，复用 tasks/ 成熟函数
  README.md         ← 步骤、dry-run 行为、产物、边界说明
```

无需修改 `run.py` 或 `registry.py`——`workflow` 子命令自动按目录发现。

### 每个 workflow 必须

- 支持 `--dry-run`（dry-run 下所有危险动作必须跳过，见第 7 节）
- step 拆分清晰，每个 step 独立写 `StepRun` 状态到磁盘
- 输出 `Artifact`（见第 6 节规范）
- 失败时返回 `failure_result(errors=...)` 给出清晰错误信息
- 危险写入必须有明确 `--execute` 或等价参数保护
- step handler 复用 `tasks/` 成熟函数，不在 workflow 层重写业务逻辑

### step 写法示例

```python
from core.runtime import StepContext, success_result, failure_result, Artifact

def my_step(ctx: StepContext):
    if ctx.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run 跳过"})

    value = do_real_work(ctx.state["source"])   # 复用 tasks/ 既有函数
    ctx.state["value"] = value
    return success_result(outputs={"count": len(value)})
```

### step 失败语义

- `required=True`（默认）：失败中断整个 workflow，TaskRun 记 `failed`
- `required=False`：失败只记 `failed`，继续后续步骤

### 验证命令

```bash
python3 run.py workflow <id> --dry-run
python3 -m pytest -q
```

### AI Knowledge Base 同步

新增、修改或废弃 workflow 后，必须同步 `<ai知识库根>`，不能只改代码不更新知识库。

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

`tasks/` 只保留旧入口兼容，**不再承载新增业务主逻辑**。

- 可作为 wrapper 调用 `run.py workflow <id>`
- 必须保留中文 alias 和旧命令习惯（不破坏现有触发词）
- 新增中文命令：在 `tasks/<name>.yaml` 声明 `name / aliases / fuzzy_keywords / entrypoint`
- `task.yaml` 自动注册，无需修改 `task_registry.py`
- tasks 平台调用统一走 `clients/ops_cli_client.py`
- tasks 不直接管理浏览器、Cookie、Token、Session
- 长期路径写入 `config/paths.yaml`，通过 `core.config_loader.get_path()` 读取

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

落盘：`runtime/runs/YYYY-MM/run_xxx/artifacts.json`

```python
art = Artifact(type="xlsx", role="output", name=p.name, path=str(p), platform="tmcs", month="5")
return success_result(artifacts=[art])
# 或：ctx.add_artifact(art)
```

---

## 7. Dry-run 安全规范

`--dry-run` 模式下，以下操作**绝对禁止**执行：

| 禁止动作 |
|---|
| 真实下载平台文件（猫超/聚水潭） |
| 真实上传/导入聚水潭 |
| 真实写 Excel 主数据 |
| 真实发送微信/企业微信通知 |
| 真实移动/删除 NAS 文件 |
| 真实修改平台订单/商品/备注 |

dry-run 必须返回明确输出，不得静默跳过。

通知统一走 `core.runtime.send_notification(content, dry_run=ctx.dry_run)`，dry-run 自动不发送。

---

## 8. 测试规范

新增功能必须至少覆盖：

1. **workflow 能注册**：`discover_workflow(<id>)` 不抛异常
2. **workflow dry-run 能跑通**：`python3 run.py workflow <id> --dry-run` 正常退出
3. **旧中文入口 dry-run 兼容**：对应中文命令 `--dry-run` 不报错
4. **Artifact 记录**：dry-run 产出 Artifact 结构正确
5. **危险动作在 dry-run 不执行**：mock 危险函数，断言 dry-run 时未调用
6. **全量测试通过**：`python3 -m pytest -q` 零失败

测试文件放 `tests/`。

---

## 9. Git 规范

- 每个功能一个 commit
- 修改前先 `git status`，修改后必须 `git diff --stat` 和 `git status`
- **不提交**：`runtime/runs/`、`logs/`、`**/cache/`、session/cookie/token 相关文件、`output/`
- 不把 Cookie / Token / Authorization 明文写入任何文档或示例

---

## 10. 禁止事项（全局红线）

| 禁止项 |
|---|
| 引入数据库（除非明确要求） |
| 引入 FastAPI / Flask / 前端框架（除非明确要求） |
| 引入 Celery / Redis / Docker / K8s（除非明确要求） |
| 大规模移动旧代码目录 |
| 删除仍被 workflow import 复用的旧函数 |
| 修改真实 session / cookie / token |
| 破坏 Ops-Cli JSON 输出契约 |
| 破坏旧中文命令（改名/删除 task.yaml alias） |
| workflow 层写平台逻辑 |
| 散落一次性脚本 |
| 绕过 WorkflowRunner 直接执行业务 |

---

## 双浏览器接口学习架构

- **主浏览器**：日常 Chrome，绑定 Codex 插件，只用于接口学习（观察请求、提取结构）；不用于正式生产执行；不复用 Cookie/Token
- **9222 Chrome**：SessionHub 专用，独立 profile，负责真实生产 session 和稳定 API 执行

新增能力时，默认让用户手动打开目标页面并完成复杂 UI 操作，Codex 只抓取接口结构、沉淀 scene。

---

## SessionHub 规则

SessionHub（位于 `Ops-Cli/sessionhub`）只做：启动/关闭 9222 Chrome、导出 Cookie、捕获请求、保存 scene/session。

SessionHub 不做：业务 Excel、猫超账单/商品、聚水潭同步、刷单、买家秀、NAS、通知。

请求捕获域名只允许：`erp321.com / jushuitan.com / tmall.com / taobao.com`

---

## run.py 边界

允许：解析任务名、调用 `task_registry.resolve_task()`、选 Python 解释器、调用任务脚本、拦截 `workflow <id>` 转 WorkflowRunner、写外层日志和 `TaskContext`。

不允许：平台接口请求、Excel 处理、复杂业务参数解析、浏览器/Cookie/Token 管理。

---

## 快速参考

```bash
# 查看所有任务
python3 run.py --list

# 运行旧中文任务（dry-run）
python3 run.py 猫超账单整理 --dry-run

# 运行 workflow（dry-run）
python3 run.py workflow tmall_monthly_bill --dry-run

# 全量测试
python3 -m pytest -q

# 查看最近运行记录
python3 run.py runs --limit 10
```

详细说明：
- Workflow runtime → `docs/workflow_runtime.md`
- 平台边界 → `docs/project_boundary.md`
- Ops-Cli 调用规范 → `docs/ops_cli_integration.md`
- 架构说明 → `docs/architecture.md`
