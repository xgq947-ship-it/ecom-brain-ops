# Skill 开发规范

## 原则

- skill 只表达业务动作
- skill 不承载平台实现细节
- skill 的正式执行入口应优先落到 `run.py` 或 `Ops-Cli`

## 禁止写死

- URL
- Cookie
- Token
- Header
- Selector
- requests 平台代码
- Playwright / CDP 细节

## 允许调用

- `python3 run.py ...`
- `ops --json ...`

## 平台响应规则

- skill 只读取 `ops --json` stdout 的结构化响应，使用 `data.error_code`、`data.context_path` 与 `data.session_recovery`。
- stderr 只记录登录等待与浏览器恢复诊断，不作为业务判断条件。
- 交互登录恢复由 `Ops-Cli` 使用 `9222` 完成；skill 不自行拉起浏览器或捕获 session。

## 同步要求

每次改动 skill，都要同步：

- `README.md`
- `SKILL.md`
- `skill.yaml`
- 对应任务文档
- 若命令口径变化，连同 `Ops-Cli` 文档一起改

## 示例

正确：

```text
更新猫超商品列表 -> run.py -> tasks/tmall_product_list/main.py -> ops --json tmcs product sync
```

错误：

```text
skill -> requests.post("https://wdksettlement.hemaos.com/...")
skill -> 直接读取 sessionhub/data/cookies/*.json
```
