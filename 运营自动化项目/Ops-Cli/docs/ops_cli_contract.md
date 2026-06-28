# Ops-Cli 调用规范

## 调用方式

统一从外部项目调用：

```bash
ops --json ...
```

不要再调用平台层散落脚本名。

## 约束

- 必须优先 `--json`
- 失败时以退出码非 0 表示
- `stdout` 始终只输出一个结果 JSON 文档，包含登录等待过程的执行也不例外
- `stderr` 留给登录提示、浏览器启动、重试过程和诊断信息

## 标准结构

```json
{
  "success": true,
  "platform": "tmcs",
  "command": "bill download",
  "data": {
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

失败响应的 `data` 至少提供 `error_code`、`retryable`、`required_scenes`、`context_path` 与 `recovery_hint`。错误码为 `AUTH_REQUIRED`、`SCENE_CAPTURE_FAILED`、`TEMPLATE_MISSING`、`PLATFORM_REQUEST_FAILED` 或 `ARTIFACT_INVALID`。

## 登录恢复策略

- 终端交互执行默认允许自动打开 `9222` 专用浏览器，等待登录后按 scene 配置捕获并只重试原操作一次。
- `--interactive-login` 与 `--no-interactive-login` 可显式覆盖 TTY 判断。
- `--dry-run` 和 `auth check` 只检查现有状态，不启动浏览器、不捕获、不下载、不写业务文件。
- 无 TTY 执行遇到失效 session 会快速返回 `AUTH_REQUIRED`，不会等待人工登录。

推广账单下载同样遵守该结构：

```json
{
  "success": true,
  "platform": "tmcs",
  "command": "promotion-bill download",
  "data": {
    "sources": [],
    "downloaded_files": [],
    "failed": []
  }
}
```

推广账单文件名统一为 `智多星推广账单_YYYY-MM.xlsx` 和 `万象台推广账单_YYYY-MM.csv`；智多星文件为平台导出的完整资金流水原表，由编排层基于账期筛选汇总，不在 CLI 层删行；如果平台后续返回 Excel 二进制，Ops-Cli 会按真实内容自动保留 `.xlsx`。

订单物流查询支持单笔与批量输入：

```bash
ops --json jst order logistics --outer-order-id TB001 --outer-order-id TB002
ops --json jst order logistics --input /path/to/orders.txt --limit 10
```

批量返回 `data.summary` 和 `data.items`；单项失败保留在 `items[].error` 中，便于业务层汇总而不中断整批查询。

## 外部编排项目应做什么

- 只负责组装业务参数
- 只负责记录业务日志
- 只负责读取 `success/platform/command/data`
- 只以 stdout JSON 契约判断失败类型，stderr 仅可记录为诊断
- 不重新解析平台 Cookie / Token

## 外部编排项目不应做什么

- 不直接 import SessionHub 内部模块
- 不直接写平台 URL
- 不直接发平台 requests
- 不直接连 Playwright / CDP

## 兼容建议

- 外部项目保留原中文任务名
- 内部统一映射到 `ops ...`
- 只在业务层做自然语言触发，不在平台层做模糊任务分发
