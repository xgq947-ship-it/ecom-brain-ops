# platforms/ — 平台插件

每个子目录是一个**平台插件**。`ops` 启动时由 `cli.py` 的
`_discover_and_register_platforms()` 自动发现 `platforms/*/platform.py` 并调用其
`register()`，**核心调度零硬编码平台名**。

## 现有插件（参考实现，可保留作样例或删除）

| 目录 | 平台 |
|---|---|
| `tmcs/` | 天猫超市 / 猫超 |
| `jst/` | 聚水潭 ERP |
| `tmall/` | 天猫公开商品页 |
| `_example/` | **示例模板**（下划线开头 → 发现逻辑自动跳过，不会加载） |

## 新增一个你自己的平台

1. **复制** `_example/` → `<你的平台名>/`（去掉下划线即激活）。
2. 在 `platform.py` 的 `register(app, capabilities)` 里：
   - 用 `typer.Typer()` 建命令组、挂命令；
   - 每条命令通过 `cli_helpers._execute(...)` 执行，handler 返回 `data` 字典；
   - 用 `capabilities["<id>"] = CapabilitySpec(...)` 声明能力（id / platform / command
     是路由真相源）。
3. 在 `../../sessionhub/config/sites/<你的平台名>.yaml` 写站点 / 场景（见 `_example.yaml`）。
4. 完成。无需改 `cli.py` / `capabilities.py` 等核心文件。

## 边界（务必遵守）

- 平台层**只**负责：平台 API、浏览器、Cookie/Token、Selector、Playwright/CDP、scene 学习，
  输出统一 JSON（`success / platform / command / data`）。
- 业务规则（Excel 编排、指标判断、通知…）**不写在这里**，放业务层 `运营自动化工具/`。
