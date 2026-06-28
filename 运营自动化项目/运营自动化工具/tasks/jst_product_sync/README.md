# 聚水潭商品资料同步

统一入口：

```bash
python3 run.py 更新聚水潭资料 --dry-run --use-local-only
```

当前实现：

- 业务入口仍在本项目
- 平台执行统一委托给 `ops --json jst product sync`
- 本任务不再自己读取 SessionHub、URL、Cookie、Header
- 真实执行首次调用 `jst` 前，公共客户端会先以 `--interactive-login jst auth ensure` 做一次预检；预检失败不执行同步。`--dry-run` 跳过预检，登录恢复和 `session_recovery` 输出由 `Ops-Cli` 处理

边界：

- 这里负责业务入口兼容
- `Ops-Cli` 负责聚水潭平台导出与同步
