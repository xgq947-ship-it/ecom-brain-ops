# Ops-Cli 架构设计

## 定位

`Ops-Cli` 是平台能力层，不是业务工作流层。

- 负责平台接入：JST、TMCS、浏览器自动化、CDP、SessionHub、请求模板、平台鉴权
- 负责统一接口：`ops ...`
- 负责统一输出：JSON、日志、上下文、截图、模板
- 不负责业务编排：Excel 汇总、买家秀、NAS 流程、自然语言分发

## 分层

```text
CLI
  -> capabilities / execution
  -> integrations
  -> platforms/jst
  -> platforms/tmcs
  -> utils
  -> output/logger/runtime_context
```

## 目录职责

- `src/ops_cli/cli.py`
  - 唯一正式 CLI 入口，自动扫描 `platforms/` 下所有 `platform.py` 并调用 `register()` 注册命令
- `src/ops_cli/cli_helpers.py`
  - 共享执行辅助函数（`_execute`、`_get_json_flag`），供各平台 `platform.py` 使用
- `src/ops_cli/capabilities.py`
  - 能力注册表（各平台通过 `register_capabilities()` 动态注册）、scene 依赖、产物类型和登录恢复策略
- `src/ops_cli/platforms/jst/platform.py`
  - JST 平台命令注册入口，导出 `register(app, capabilities)` 函数
- `src/ops_cli/platforms/tmcs/platform.py`
  - TMCS 平台命令注册入口，导出 `register(app, capabilities)` 函数
- `src/ops_cli/execution.py`
  - 统一执行生命周期、JSON 契约、错误分类、context 和恢复摘要
- `src/ops_cli/integrations/sessionhub.py`
  - 统一接 SessionHub scene 校验 / capture / ensure
- `src/ops_cli/platforms/jst/`
  - 聚水潭全部平台逻辑
- `src/ops_cli/platforms/tmcs/`
  - 猫超全部平台逻辑
- `src/ops_cli/output.py`
  - 统一 JSON 输出结构
- `src/ops_cli/logger.py`
  - 统一日志格式
- `src/ops_cli/runtime_context.py`
  - 统一上下文记录

## 执行生命周期

- `ops --json ...` 所有正式命令先进入 capability runner，再调用平台模块。
- 交互终端下，失效 scene 可自动打开 `9222`、等待手动登录、执行配置动作、复检并只重试原操作一次。
- "平台读取 + workflow 业务判断"类功能（如猫超物流履约监控 `tmcs fulfillment overview`）：进入后台、页面跳转、读取数据概览等读取动作一律放本层，只输出原始数值的统一 JSON；指标合格判断、预警分级、通知全部留给业务层 workflow。
- `--dry-run` 与 `auth check` 仅检查当前状态，不启动浏览器、不捕获、不写业务产物。
- 无 TTY 或 `--no-interactive-login` 失效时快速返回 `AUTH_REQUIRED`；`--interactive-login` 可强制启用交互恢复。
- stdout 只允许一个 JSON 文档；登录提示、浏览器启动和重试过程只写 stderr 与 runtime context。

## 边界

以下代码只能放在 `Ops-Cli`：

- 平台 URL
- Cookie / Token / LocalStorage
- Playwright / CDP / 浏览器页面操作
- Selector / 页面点击 / 上传下载
- requests / httpx 直接请求平台
- Scene 学习与请求重放

以下代码不能再放回 `运营自动化工具`：

- `requests.post("https://www.erp321.com/...")`
- `requests.post("https://wdksettlement.hemaos.com/...")`
- `browser_cookie3`
- `connect_over_cdp`
- `sessionhub` 内部 import

## 调用关系

```text
运营自动化工具/run.py
  -> tasks/*
  -> subprocess
  -> ops --json ...
  -> Ops-Cli
```

## 当前兼容点

- `SESSIONHUB_ROOT` 当前默认指向 `<项目根>/Ops-Cli/sessionhub`
- `sessionhub/scene`、`browser`、`config` 和入口代码纳入仓库；Cookie、session、日志和浏览器 profile 永久排除
- 这是平台能力目录，不再意味着平台逻辑归业务项目
