# jst_brush_reimburse_workorder workflow

把「聚水潭刷单报销工单/登记」从单脚本升级为 step 化流程。这是既有任务的**包装层**，不替代旧命令。

## 入口

旧命令（不变，走 `tasks/jst_brush_reimburse_workorder.py`）：

```bash
python3 run.py 刷单报销登记 --dry-run
python3 run.py 刷单报销登记 --order-no 3302371490526182153
```

新 workflow 入口：

```bash
python3 run.py workflow jst_brush_reimburse_workorder --dry-run
python3 run.py workflow jst_brush_reimburse_workorder --order-no 3302371490526182153   # 真实提交
```

支持参数（透传给复用逻辑）：`--input`（登记表路径）、`--order-no`、`--dry-run`。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析参数、定位登记表 | 文件缺失时安全跳过 |
| `load_reimburse_data` | 读取当前未标记批次（`read_current_batch`） | 只读 |
| `validate_amounts` | 计算本金/打款合计 | 只读 |
| `preview_workorder` | 核验候选工单（Ops-Cli `execute=False` 只读查询） | 只读查询 |
| `submit_workorder` | 提交报销工单（Ops-Cli `--execute`） | **跳过，不提交真实工单** |
| `update_register` | 备份 + 写黄色标记行（ZIP/XML 补丁） | **跳过，登记表零改写** |
| `collect_artifacts` | 汇总结果、失败导出 | dry-run 不写任何文件 |

## dry-run 安全策略（关键）

1. **不提交真实工单**：`submit_workorder` 在 dry-run 下跳过，绝不向 Ops-Cli 传 `--execute`。
2. **登记表零改写**：`update_register` 在 dry-run 下跳过，不调用 `backup_workbook`、不调用 `write_marker_row`，因此登记表及其**图片 / DISPIMG / cellimages 结构**在 dry-run 下不会被触碰。
3. **不写文件**：dry-run 不生成失败导出。
4. 真实回写登记表沿用 reimburse.py 的 ZIP/XML 局部补丁（`write_marker_row`），只改 `sheet1.xml` / `styles.xml`，不用 openpyxl 整本覆盖，保留原文件图片结构。

## 真实执行需要的参数

- 默认读取当月登记表 `天猫超市{月}月刷单登记明细.xlsx`，可用 `--input` 覆盖。
- 真实提交工单 + 回写标记：去掉 `--dry-run`。可用 `--order-no` 限定批次内某单。

## 边界

- 不写平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作经 `clients/ops_cli_client.py` 调 `ops --json jst order reimburse`，在同包 `reimburse.py` 内完成。
- 业务逻辑在同包 `reimburse.py`（`read_current_batch / choose_candidate / ops_reimburse_payload / backup_workbook / write_marker_row / write_failed_export` 等）；`tasks/jst_brush_reimburse_workorder.py` 仅为透传薄 wrapper。
