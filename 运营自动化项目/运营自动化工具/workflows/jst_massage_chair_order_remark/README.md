# jst_massage_chair_order_remark

聚水潭按摩椅订单自动备注 workflow。

入口：

```bash
python3 run.py workflow jst_massage_chair_order_remark --dry-run
python3 run.py workflow jst_massage_chair_order_remark --execute
python3 run.py 按摩椅订单自动备注 --dry-run
python3 run.py 按摩椅订单自动备注 --execute
```

默认筛选近 48 小时内的数据：未显式传 `--date` 时，自动查询“今天 + 昨天”两天；店铺、状态、商品关键词从 `config/jst_massage_chair_order_remark.json` 读取（默认店铺 `（猫超）福安市启明工贸有限公司（肖国清）`、状态 `已付款待审核,异常`、关键词 `按摩椅`），读取 `config/paths.yaml` 的 `massage_chair_mapping_file`（默认 `主数据/按摩椅资料表.xlsx`）。如果显式传 `--date`，则只按该单日查询。

业务参数配置（`config/jst_massage_chair_order_remark.json`）：

| 字段 | 说明 |
|---|---|
| `shop_name` | 店铺名称（聚水潭店铺全称） |
| `status` | 订单状态过滤，多值逗号分隔；「异常」状态订单也纳入备注，平台层按订单 status 精确匹配 |
| `keyword` | 商品关键词 |

配置文件缺失或字段缺失时回退到内置默认值（零配置即可跑）；命令行 `--shop-name / --status / --keyword` 仍可临时覆盖配置。

步骤：

1. `check_inputs`：解析并校验参数。
2. `fetch_orders`：调用 `ops --json jst order query` 查询订单。
3. `load_massage_chair_mapping`：读取本地资料表，构建商品编码到商品名称的映射。
4. `build_remark_plan`：跳过已有备注、缺少编码、资料表查不到、无法明确匹配的订单。
5. `apply_remarks`：只有 `--execute` 才调用 `ops --json jst order remark --execute`。
6. `normalize_abnormal_orders`：对「**异常状态 + 本次成功备注**」的订单调 `ops --json jst order normalize --execute`（聚水潭「转正常单」/ UnQuestions），把异常单转回正常；本身不是异常的不处理；`--execute` 才真正转。
7. `collect_outputs`：输出汇总和 `remark_plan`（含 `abnormal_remarked_count` / `normalized_count` / `normalize_failed_count`）。

边界：

- workflow 不直接请求聚水潭，不写 URL、Cookie、Token、Selector、Playwright 或 CDP。
- dry-run 只查询和生成计划，不写聚水潭。
- 不覆盖已有备注。
