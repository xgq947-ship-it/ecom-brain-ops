# 猫超商品信息同步聚水潭 Skill 正式化设计

## 目标

将现有 `skills/tmcs_sync_jst_shop_goods` 升级为项目内正式、可复用、可自然语言触发的业务 skill。用户输入一个或多个猫超平台商品 ID 后，可通过统一入口查询猫超库存映射，生成聚水潭导入表，并在明确执行模式下导入聚水潭店铺商品资料。

## 范围

- 复用现有 `tmcs_sync_jst_shop_goods` 的输入处理、Excel 生成和 `Ops-Cli` 客户端编排代码。
- 增加正式业务任务入口，并注册到 `run.py` 解析链路。
- 增加标准 `SKILL.md`，覆盖用户常用中文触发语义。
- 同步根目录 `README.md`、项目 `SKILL.md`、skill 元数据和必要测试。

不新增平台请求、浏览器操作、Cookie、Token 或 selector 逻辑。猫超查询和聚水潭导入仍只由 `Ops-Cli` 负责。

## 架构

正式链路：

```text
用户触发
  -> 运营自动化工具 skill / run.py
  -> task adapter
  -> skills/tmcs_sync_jst_shop_goods 现有业务模块
  -> clients/ops_cli_client.py
  -> Ops-Cli
       -> tmcs stock query
       -> jst shop-goods import
```

`task adapter` 只负责把统一入口参数传递给现有业务模块，并让 `run.py` 统一生成外层日志和 `runtime/context`。现有 skill 目录仍保留业务专用模块，避免复制一套 Excel 映射实现。

## 入口与触发

新增统一任务名：`tmcs_sync_jst_shop_goods`。

中文 aliases：

- `聚水潭商品信息同步猫超`
- `猫超商品信息同步聚水潭`
- `平台商品ID同步聚水潭`
- `猫超商品同步聚水潭`

执行示例：

```bash
python3 run.py 聚水潭商品信息同步猫超 --item-ids 1052305450766 --import-jst --import-mode cover
```

自然语言中若包含 `平台商品ID + 聚水潭 + 猫超 + 同步`，由 skill 指引代理选择上述入口，并默认执行真实导入；仅当用户明确要求预览时使用 `--no-import`。

## 数据流

1. 接收 `--item-ids` 或带平台商品 ID 的 Excel。
2. 调用 `ops --json tmcs stock query` 获取标准字段：`platform_item_id`、`platform_sku_id`、`supplier_goods_id`、`merchant_goods_code`。
3. 将有效记录映射成聚水潭「导入店铺商品资料」Excel。
4. 无有效行时写失败表并终止导入。
5. `--import-jst` 时调用 `ops --json jst shop-goods import --mode cover`，店铺默认使用页面实际有效名称 `（猫超）福安市启明工贸有限公司（肖国清）`。

## 结果与错误

正常结果必须回报：

- 请求的平台商品 ID。
- 猫超返回行数、聚水潭导入成功数和失败数。
- 导入 Excel 路径、失败 Excel 路径（如有）。
- 业务日志路径及 `Ops-Cli` 的 context / 截图路径（如有）。

认证失效、scene 失效和页面导入错误沿用 `Ops-Cli` 的 `AUTH_REQUIRED` / `session_recovery` / `context_path` 结构，不在业务 skill 中另造平台恢复逻辑。

## 测试

- 任务注册与中文别名能够解析到统一入口。
- task adapter 能转发 `--item-ids`、`--import-jst`、`--import-mode` 等参数。
- 现有商品 ID 解析、Excel 映射、公共交互恢复测试继续通过。
- 用已验证的平台商品 ID 运行正式入口，验收猫超查询返回记录并取得聚水潭导入回执。

## 已确认决策

- 升级现有 `tmcs_sync_jst_shop_goods`，不创建第二套同步实现。
- 业务层仅做编排，平台能力继续沉到 `Ops-Cli`。
- 正式能力接入 `run.py`，而不是继续保留为只能直接运行目录脚本的旁路入口。
