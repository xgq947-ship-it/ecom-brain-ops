# 运营自动化工具架构说明

## 定位

这是业务编排层，不是平台层。

## 只允许做的事

- skill 触发
- 业务工作流编排
- Excel 生成 / 回写
- 路径管理
- 日志与上下文记录
- retry queue
- subprocess 调 `Ops-Cli`

## 禁止做的事

- 平台 API 请求
- Cookie / Token 管理
- Playwright / CDP / 浏览器控制
- Selector / 页面 URL
- SessionHub 内部导入

## 当前结构

```text
run.py
  ├─ <中文任务名>
  │    -> core/task_registry.py (扫描 tasks/ 下的 task.yaml 动态加载)
  │    -> tasks/*
  │    -> clients/ops_cli_client.py -> ops --json ... -> Ops-Cli capability runner
  └─ workflow <id>
       -> core/runtime/registry.py (扫描 workflows/<id>/workflow.py)
       -> core/runtime WorkflowRunner
       -> workflows/<id>/steps.py (复用 tasks/* 成熟函数)
```

## 两条执行链路

- 旧链路：中文任务名 / 别名 / 模糊触发 -> `tasks/*` 单脚本执行，落 `runtime/context`。保持不变。
- 新链路：`workflow <id>` -> `workflows/*` step 化流程执行，落 `runtime/runs/YYYY-MM/run_xxx/`（`run.json` + `steps/<id>.json` + `artifacts.json`）。
- workflow 是包装层，复用 `tasks/` 的成熟实现，不重写业务逻辑；平台动作仍统一通过 `clients/ops_cli_client.py` 调 `Ops-Cli`。
- 边界与旧链路一致：业务层（`tasks/` 与 `workflows/`）都不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub。
- 细节见 [workflow runtime 说明](workflow_runtime.md)。

## "平台读取 + workflow 业务判断"类功能

部分功能只是读取平台数据后由业务层判断，例如已落地的猫超物流履约监控（workflow_id `tmcs_fulfillment_watch`，中文入口 `猫超履约监控`）：

- 平台读取（进入后台、页面跳转、读取「数据概览」）放 `Ops-Cli`，统一走 `ops --json tmcs fulfillment overview`。
- workflow 只负责考核指标判断、观测指标判断、周数据预警等级判断与通知预览。
- 无风险默认不输出通知，只记录运行结果；dry-run 只预览，不发送通知、不处理平台数据。
- 通知放 workflow 的 notify step，统一走 `send_notification`。

## 说明

`sessionhub/` 已迁移到 `Ops-Cli/sessionhub`；本项目不再保存平台会话资产。

业务侧只解析 `ops --json` stdout 中的 `success/data/error_code/context_path/session_recovery`。真实 `jst` / `tmcs` 请求由公共客户端在当前进程内按平台先调用一次 `--interactive-login ... auth ensure`，预检失败即停止业务动作；预检后再次出现 `AUTH_REQUIRED` 时，交互终端调用再追加 `--interactive-login` 重试一次。登录等待、浏览器启动和 scene 恢复仍由 `Ops-Cli` 处理，本项目不解析 stderr 文案，也不直接操作 session。
