# SessionHub

SessionHub 是本项目的统一登录态中心，只负责基础会话能力，不承载具体业务逻辑。

核心职责：

- 启动、关闭、检查 9222 专用 Chrome
- 复用统一 Chrome 用户目录：`~/.sessionhub/chrome-9222`
- 导出猫超和聚水潭 Cookie
- 检查猫超和聚水潭登录状态
- 捕获指定域名下的接口请求
- 输出统一 session 文件，供 `tasks/` 下的业务脚本读取

SessionHub 不做：

- 不写业务 Excel
- 不处理猫超账单、商品表、聚水潭打标等业务逻辑
- 不引入数据库
- 不做前端页面
- 不启动复杂服务

## 目录结构

```text
sessionhub/
  browser/
    start_chrome_9222.sh
    stop_chrome_9222.sh
    status_chrome_9222.sh
  session/
    export_cookies.py
    check_login.py
    refresh_session.py
  capture/
    capture_requests.py
    filter_rules.yaml
  data/
    cookies/
    sessions/
    requests/
  logs/
  config.yaml
  api.py
```

## Chrome

启动专用 Chrome：

```bash
cd <项目根>/Ops-Cli/sessionhub
./browser/start_chrome_9222.sh
```

检查状态：

```bash
./browser/status_chrome_9222.sh
```

关闭专用 Chrome：

```bash
./browser/stop_chrome_9222.sh
```

固定参数：

```text
端口：9222
用户目录：~/.sessionhub/chrome-9222
```

## Cookie

导出猫超 Cookie：

```bash
python3 session/export_cookies.py tmall --start-browser
```

导出聚水潭 Cookie：

```bash
python3 session/export_cookies.py jst --start-browser
```

保存位置：

```text
sessionhub/data/cookies/tmall.json
sessionhub/data/cookies/jst.json
```

## 登录检查

检查全部平台：

```bash
python3 session/check_login.py all
```

检查猫超：

```bash
python3 session/check_login.py tmall
```

检查聚水潭：

```bash
python3 session/check_login.py jst
```

刷新并检查：

```bash
python3 session/refresh_session.py tmall
python3 session/refresh_session.py jst
```

检查结果会保存到：

```text
sessionhub/data/sessions/health_status.json
```

日志保存到：

```text
sessionhub/logs/check_login.log
```

`auth check` 仅检查当前状态，不自动启动浏览器。通过 `ops --json ...` 正式交互执行平台命令时，如 session 失效，capability runner 会启动 `9222` 专用 Chrome、等待手动登录、按 scene 的 `auto_actions` 触发请求并继续原命令；无 TTY 或 `--no-interactive-login` 执行会直接返回鉴权失败。

## Scene 配置

每个 scene 统一支持：

- `target_url`、`match_url_contains`、`method`
- `auto_actions`：`goto_target`、`reload`、`click_text`、`click_any_text`
- `wait_seconds`：等待登录和请求捕获时间
- `capture_retry_limit`：自动固定动作的最大执行次数
- `sensitive_artifact_policy`：鉴权产物存储策略，默认 `local_ignored`

`data/cookies/`、`data/sessions/`、`logs/` 和浏览器 profile 均为本地敏感或运行资产，不进入 Git。

## 请求捕获

监听当前 9222 Chrome，不主动打开页面：

```bash
python3 capture/capture_requests.py
```

捕获猫超请求：

```bash
python3 capture/capture_requests.py tmall --wait 90
```

捕获聚水潭请求：

```bash
python3 capture/capture_requests.py jst --wait 90
```

只捕获 URL 包含指定片段的请求：

```bash
python3 capture/capture_requests.py tmall --contains DownloadFile --wait 90
```

允许捕获的域名只来自：

```text
erp321.com
jushuitan.com
tmall.com
taobao.com
```

请求记录保存到：

```text
sessionhub/data/requests/requests.jsonl
```

查看最近捕获：

```bash
tail -n 20 sessionhub/data/requests/requests.jsonl
```

每条记录包含：

```json
{
  "captured_at": "",
  "platform": "",
  "method": "",
  "url": "",
  "path": "",
  "query": {},
  "headers": {},
  "request_body": {},
  "status": null,
  "response_preview": "",
  "content_type": "",
  "source": "chrome_9222"
}
```

`cookie`、`authorization`、`token`、`x-csrf-token`、`x-xsrf-token` 会自动脱敏为 `***REDACTED***`。

捕获日志保存到：

```text
sessionhub/logs/capture.log
```

最新 session 保存到：

```text
sessionhub/data/sessions/latest_session.json
```

## Python API

业务脚本优先使用：

```python
from sessionhub.api import get_session, load_cookies, check_login

session = get_session("tmall")
cookies = load_cookies("tmall")
status = check_login("tmall")
```

动态 scene session 入口：

```python
from scene.api import get_session

session = get_session("tmall_chaoshi", "download_file_query")
```

scene 入口会继续服务当前已跑通的猫超账单、猫超商品列表、聚水潭商品资料导出等任务，同时同步写入 `data/cookies/` 和 `data/sessions/latest_session.json`。
