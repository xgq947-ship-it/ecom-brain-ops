# 猫超商品信息同步聚水潭

统一入口：

```bash
python3 run.py 聚水潭商品信息同步猫超 --item-ids 1052305450766 --import-jst --import-mode cover
```

当前实现：

- `tasks/` 仅提供统一调度适配，复用 `skills/tmcs_sync_jst_shop_goods` 的现有业务实现。
- 猫超商品查询委托给 `ops --json tmcs stock query`。
- 聚水潭导入委托给 `ops --json jst shop-goods import`。
- 支持多个平台商品 ID 逗号分隔输入，也支持 `--input-file` 批量读取 Excel。

边界：

- 本任务负责商品 ID 输入、导入表生成、执行编排和结果汇总。
- `Ops-Cli` 负责平台 API、登录恢复、scene 和聚水潭页面写入。
