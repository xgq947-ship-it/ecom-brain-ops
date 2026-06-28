# 电商运营自动化框架包

一套可移植的**电商运营自动化框架** + 配套 **AI 知识库**。把"平台能力 / 业务编排 / 知识沉淀"
按清晰分层组织，换电脑或别人 clone 后，搭好环境即可运行（见 [`首次安装指南.md`](首次安装指南.md)）。

> 本包是从一个真实运营项目**提取出的干净骨架**：不含任何账号登录态、业务主数据、
> 个人知识正文。具体业务（猫超 / 聚水潭等）以**参考插件**形式附带，可保留作样例或替换为你自己的平台。

---

## 目录结构

```text
运营框架包/
├── README.md            ← 本文件（总览）
├── 首次安装指南.md       ← 从零搭建步骤（环境 / 依赖 / 登录态 / 验证）
├── 运营自动化项目/       ← 代码主体（平台层 + 业务层）
└── ai知识库/            ← 配套知识库骨架（结构 + 机制，正文由 workflow 生成）
```

---

## 架构分层（核心思想）

代码主体是**两层 + 严格单向依赖**，这是整个框架可维护、可移植的根基：

```text
Ops-Cli/                    ← 平台能力层（唯一允许碰平台的代码）
  src/ops_cli/platforms/    ← 平台插件（jst / tmall / tmcs…），自动发现、自动注册
  sessionhub/               ← 浏览器 9222 / Cookie / Session / scene 学习引擎
        ▲
        │ 只通过 `ops --json` 单一契约调用
        │
运营自动化工具/              ← 业务编排层
  core/runtime/             ← WorkflowRunner 内核（零业务、零平台）
  core/business_paths.py    ← 业务路径表（可整体替换）
  core/business_text.py     ← 业务文案（可整体替换）
  core/runtime/notify_backends.py ← 通知后端（可整体替换）
  workflows/                ← step 化业务流程
  tasks/                    ← 旧中文命令兼容入口
```

| 层 | 职责 | 红线 |
|---|---|---|
| **Ops-Cli** | 平台 API、浏览器、Cookie/Token、Selector、Playwright/CDP、scene 学习 | 不碰业务 Excel / 业务规则 |
| **运营自动化工具** | workflow 编排、产物记录、指标判断、通知 | 不碰平台 URL/Cookie/Token/Playwright |
| **core/runtime** | 运行时内核 | 不含业务、不含平台 |

业务层只消费 `ops --json` 的单一 JSON 文档（`success / platform / command / data`），
从不直连平台、不管 session——这层契约让两边能各自演进、各自替换。

---

## 三个"为分发而设计"的特性

1. **零配置路径**：所有路径从锚点推导（不写死 `/Users/<name>/...`），换机即用；
   非标准布局可用 `config/paths.local.yaml` 或环境变量覆盖。
2. **插件式平台**：加新平台 = 丢一个 `platforms/<x>/platform.py` + `config/sites/<x>.yaml`，
   核心调度自动发现，**引擎一行不用改**。
3. **业务可替换**：业务内容集中在三个标注「可整体替换」的文件
   （`business_paths.py` / `business_text.py` / `notify_backends.py`），换业务不动引擎。

---

## 快速开始

1. 按 [`首次安装指南.md`](首次安装指南.md) 装好两层 venv、浏览器、路径与登录态。
2. 看所有任务：`cd 运营自动化项目/运营自动化工具 && python3 run.py --list`
3. 安全试跑（不触发真实动作）：`python3 run.py workflow <id> --dry-run`
4. AI 知识库用法见 [`ai知识库/README.md`](ai知识库/README.md)。

---

## 安全约定（务必遵守）

- **绝不提交**：Cookie / Token / Authorization / session / 真实主数据 / `paths.local.yaml` /
  `*.secret.env` / `runtime/runs` / `logs`（`.gitignore` 已覆盖）。
- 每台机器**自己抓登录态**（scene 学习），框架不附带任何人的 Cookie。
- `--dry-run` 永远安全：不下载、不写主数据、不发通知、不动 NAS、不改平台数据。
