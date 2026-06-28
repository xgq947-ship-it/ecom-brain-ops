# 猫超商品列表更新

统一入口：

```bash
python3 run.py 更新猫超商品列表 --dry-run --skip-auto-download
```

当前实现：

- 业务入口仍在本项目
- 平台执行统一委托给 `ops --json tmcs product sync`
- 本任务不再自己调用 SessionHub、导出接口、URL 或 requests
- 真实执行首次调用 `tmcs` 前，公共客户端会先以 `--interactive-login tmcs auth ensure` 做一次预检；预检失败不执行同步。`--dry-run` 跳过预检，登录恢复和 `session_recovery` 输出由 `Ops-Cli` 处理
- 处理后的主表默认写到 `02-运营店铺/主数据/猫超商品列表导出 (最新）.xlsx`

边界：

- 这里负责业务任务名兼容
- `Ops-Cli` 负责猫超商品导出、条码修复、主表同步
