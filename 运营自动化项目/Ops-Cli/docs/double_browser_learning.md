# 双浏览器学习方案

## 标准口径

新增接口能力、学习真实接口、沉淀 scene、后续转 API 执行时，统一采用标准双浏览器方法。

## 两个浏览器的职责

### 1. 主浏览器

- 普通 Google Chrome Default profile
- 指你本机日常使用的 Google Chrome
- 由人工操作或 Codex Chrome 插件接管
- 用于真实页面探测、真实交互、真实请求观察
- 只有主浏览器暴露了可接管入口时，Ops-Cli 才能自动捕获网络请求

### 2. 9222 浏览器

- SessionHub 专用浏览器
- 固定负责 scene 沉淀、复检、长期执行
- 交互执行中也负责失效 session 的登录等待与自动恢复
- 不承担主浏览器探测角色

## 禁止事项

- 不要把只用 `9222` 的 capture 流程称为双浏览器
- 不要直接读取主浏览器 Cookie/Token 到业务脚本
- 不要在业务项目里自己写 CDP / Playwright 探测逻辑

## 典型流程

```text
主浏览器观察真实行为
-> 确认 scene
-> 9222 SessionHub 复刻并沉淀
-> Ops-Cli 持续执行
```

## 正式执行恢复

正式执行不是主浏览器学习流程。`Ops-Cli` 的 capability runner 在交互终端发现 scene 失效时，只操作 `9222` 专用浏览器：打开目标页、等待手动登录、执行 `reload/click_text/click_any_text`、捕获并重试一次。`--dry-run`、`auth check` 和无 TTY 调用不进入该流程。

## 推荐命令

```bash
ops --json browser check --port 9222
ops --json jst browser learn --scene shop-goods-import
ops --json --interactive-login tmcs bill learn
ops --json --interactive-login tmcs product learn
```
