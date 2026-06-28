---
name: ecommerce-operations-automation
description: Use when the user mentions recurring local ecommerce operations in <电商Brain根>, including semantically similar wording for 刷单登记, 猫超账单/对账, 猫超商品列表, 聚水潭商品资料, 买家秀, 公司网盘/NAS产品资料, 上架数据, or asks to run or extend the shared run.py automation entry point.
---

# 运营自动化工具 Skill

这个 skill 只维护业务编排层：

```text
<项目根>/运营自动化工具
```

## 核心边界

- 允许：
  - `run.py`
  - `tasks/`
  - Excel 处理
  - 业务规则
  - 上下文 / retry / 日志
  - `subprocess` 调 `ops --json ...`
- 禁止：
  - 平台 URL
  - Cookie / Token
  - requests 直连平台
  - Playwright / CDP / 浏览器页面操作
  - Selector
  - 直接 import `sessionhub/*`

## 新任务规则

1. 新任务先放 `tasks/`
2. 在任务目录创建 `task.yaml` 声明任务名、aliases、fuzzy_keywords、required_modules、entrypoint（自动注册）
3. 优先接 `run.py`
4. 平台动作统一放到 `Ops-Cli`
5. 业务层通过 `clients/ops_cli_client.py` 调 `ops --json ...`
6. 更新 `README.md`、`SKILL.md`、相关 `skill.yaml`

`ops --json` 的 stdout 只能按标准 JSON 解析；登录等待、浏览器启动和恢复过程属于 stderr 诊断信息，业务任务不得依赖其文案。终端交互登录恢复由 `Ops-Cli` 处理，业务层不自行接管浏览器。

## 平台调用规则

业务任务应调用：

```bash
ops --json ...
```

不要调用：

```bash
python sessionhub.py ...
python demo.py ...
python browser_test.py ...
```

新增 `Ops-Cli` 平台接口能力时，默认先让用户在主浏览器手动打开目标页面，并完成登录、店铺切换、筛选、下拉框、弹窗等复杂 UI 操作。Codex 只观察当前页面、抓关键请求、提取接口结构、沉淀 SessionHub scene，并写 `Ops-Cli` 命令；不要从平台首页长时间试错找路径。

## 当前已同步的关键任务

- `更新聚水潭资料` -> `ops --json jst product sync`
- `更新猫超商品列表` -> `ops --json tmcs product sync`
- `聚水潭商品信息同步猫超` -> `ops --json tmcs stock query` + `ops --json jst shop-goods import`
- `刷单订单插黄旗` -> `ops --json jst order label`
- `刷单报销登记` -> `ops --json jst order reimburse`
- `猫超账单下载阶段` -> `ops --json tmcs bill download`
- `聚水潭揽收监控` -> `ops --json jst order pickup-watch --hours 48`

真实执行上述依赖平台 session 的任务时，`clients/ops_cli_client.py` 会在每个平台首次业务请求前自动执行一次 `--interactive-login ... auth ensure` 预检，手动执行与后台自动化都生效；同一进程同一平台仅预检一次。`--dry-run` 和 `auth` 命令跳过前置预检；预检后业务调用仍返回 `AUTH_REQUIRED` 时，交互终端会再以 `--interactive-login` 重试一次，由 `Ops-Cli` 完成 SessionHub 恢复。

## 触发方式

保留模糊触发，不要求精准匹配。

- `刷单表格登记`
- `刷单订单插黄旗`
- `更新聚水潭资料`
- `更新猫超商品列表`
- `聚水潭商品信息同步猫超`
- `猫超商品信息同步聚水潭`
- `猫超账单整理`
- `买家秀打包`
- `更新公司网盘索引`
- `聚水潭揽收监控`

## 文档同步要求

每次改入口或边界，都要同步：

- 根目录 `README.md`
- 当前 `SKILL.md`
- 对应任务 `README.md`
- `docs/` 下的架构 / 边界 / 调用规范
- 对应 `skill.yaml`
