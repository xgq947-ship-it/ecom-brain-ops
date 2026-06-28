# 9222 浏览器调试说明

## 定位

`9222` 只服务于 SessionHub 专用浏览器。

- 用于 `ensure / capture / recheck`
- 用于长期稳定执行
- 不用于主浏览器探测

## 快速检查

```bash
ops --json browser check --port 9222
```

## 使用原则

- 9222 profile 固定隔离
- 平台登录后由 Ops-Cli / SessionHub 复用
- 业务脚本不能直接读取 9222 会话文件

## 推荐恢复方式

只检查状态，不触发登录恢复：

```bash
ops --json browser check --port 9222
ops --json tmcs auth check
ops --json jst auth check
```

在交互终端触发登录恢复：

```bash
ops --json --interactive-login tmcs auth ensure
ops --json --interactive-login jst auth ensure
```

正式业务命令无需预先调用 `auth ensure`；scene 失效时 capability runner 会按同样流程恢复，并只重试业务操作一次。

## 非交互行为

- `--dry-run` 与 `auth check` 不启动浏览器、不等待登录。
- 定时任务、retry queue 或 `--no-interactive-login` 遇到失效 session 时立即返回 `AUTH_REQUIRED`。
- `--json` stdout 始终只有结构化结果；登录提示与恢复进度输出到 stderr。

## 当前兼容路径

当前 `SESSIONHUB_ROOT` 默认指向：

```text
<项目根>/Ops-Cli/sessionhub
```

该目录中的代码和 scene 配置归 `Ops-Cli` 版本管理；`data/cookies`、`data/sessions` 与 `logs` 不进入 Git。
