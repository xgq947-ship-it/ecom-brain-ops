# tmcs_sync_jst_shop_goods workflow

把「猫超商品信息同步聚水潭」从 skill 脚本升级为 step 化流程。这是既有 skill 的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/tmcs_sync_jst_shop_goods/main.py` → skill）：

```bash
python3 run.py 聚水潭商品信息同步猫超 --item-ids 1052305450766
python3 run.py 聚水潭商品信息同步猫超 --item-ids 1052305450766 --import-jst --import-mode cover
```

> 注意：旧 skill **不支持 `--dry-run`**（传了会被 argparse 拒绝，这是迁移前就有的行为，本次不改动）。

新 workflow 入口：

```bash
python3 run.py workflow tmcs_sync_jst_shop_goods --dry-run
python3 run.py workflow tmcs_sync_jst_shop_goods --item-ids 1052305450766 --dry-run
python3 run.py workflow tmcs_sync_jst_shop_goods --item-ids 1052305450766 --import-jst --import-mode cover
```

支持参数（透传给 skill 实现）：`--item-ids`、`--input-file`、`--warehouse-code`、`--shop-name`、`--import-mode {ignore,cover}`、`--import-jst`、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数 | 只解析 |
| `load_tmcs_goods` | 解析/去重商品ID（`input_loader`） | 只解析，无平台调用 |
| `query_tmcs_stock` | 经 Ops-Cli 查询猫超库存（只读） | **跳过，不查询真实平台** |
| `build_jst_import_excel` | 生成聚水潭导入 Excel + 桌面「猫超商品对应关系导入表」（`excel_builder`） | **跳过，不生成 Excel** |
| `import_jst_shop_goods` | 经 Ops-Cli 导入聚水潭店铺商品 | **跳过，不导入** |
| `collect_artifacts` | 汇总产物与结果 | 只汇总 |

## dry-run 安全策略

旧 skill 必须有 `--item-ids/--input-file` 才能查询，且无 `--dry-run`。因此本 workflow 的 dry-run 是**安全预览**：

1. **零平台调用**：dry-run 不查询猫超库存、不导入聚水潭。
2. **不写 Excel**：dry-run 不生成导入表。
3. 只解析并回显将要处理的商品ID数量、仓库、店铺、导入模式。
4. 真实执行才查询库存、生成 Excel；**导入聚水潭仍必须显式 `--import-jst`**（沿用 skill 安全策略）。

## 真实执行需要的参数

- 必填其一：`--item-ids` 或 `--input-file`。
- 如需真正写入聚水潭：追加 `--import-jst`（可配 `--import-mode cover`）。

## 产物

- 聚水潭导入 Excel：`role=import`（成功表）、`role=failed`（失败表，若有），`platform=jst`，落 skill `output/`。
- **猫超商品对应关系导入表**：`role=correspondence`，落**桌面**（`desktop_dir`），固定文件名 `猫超商品对应关系导入表.xlsx`（每次覆盖）。列对齐用户模板：`平台店铺款式编码 / 平台店铺商品编码 / 线上商品名称 / 线上颜色规格 / 线上款式编码 / 线上商品编码 / 对应商品编码`。
  - `对应商品编码` = `线上商品编码` 经**条码规则**得出：归一化（去 `.0`、转大写）后到《聚水潭商品资料（最新）.xlsx》的「商品编码」池做 精确 / 唯一模糊（互为子串）匹配，命中则取聚水潭编码，多义/未命中保留归一化原值。规则与「更新猫超商品列表」的【条码】一致。
  - `线上商品名称 / 线上颜色规格` 当前猫超库存接口未返回，留空（与导入表一致）。
  - 次级产物：生成失败不阻断主同步，错误记入该 step 的 `correspondence_error`。

> 对应表读取的聚水潭主数据来自 `config/paths.yaml` 的 `jst_product_file`，桌面路径来自 `desktop_dir`。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作全部经 `clients/ops_cli_client.py` 调 `ops --json tmcs stock query` / `ops --json jst shop-goods import`（由 skill `cli_client.py` 完成）。
- 复用 skill 的 `input_loader / excel_builder / cli_client / config`，不重写业务算法。
