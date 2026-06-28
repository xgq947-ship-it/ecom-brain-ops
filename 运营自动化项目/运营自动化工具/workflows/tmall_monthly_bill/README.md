# tmall_monthly_bill workflow

把「猫超月账单整理」从单脚本执行升级为 step 化流程执行。这是 workflow runtime 的第一个业务 workflow，作为既有任务的**包装层**，不替代旧命令。

## 与旧任务的关系

- 旧命令仍然有效，逻辑不变：
  ```bash
  python3 run.py 猫超账单整理
  python3 run.py 猫超账单整理 --dry-run
  ```
  它经 `tasks/tmall_monthly_bill/main.py` 薄 wrapper 透传到 workflow。
- 新命令（workflow 包装层）：
  ```bash
  python3 run.py workflow tmall_monthly_bill --dry-run
  python3 run.py workflow tmall_monthly_bill --month 2026-05 --dry-run
  ```
  它把同一条流水线拆成步骤，逐步落运行记录到 `runtime/runs/`。

两者共用同一套成熟业务实现（`main.py` / `processor.py` / `services/`），workflow 层只做编排，不重写任何账单解析、Excel 加工或平台下载逻辑。

## 步骤

| step | 作用 | dry-run 行为 |
|------|------|--------------|
| `check_inputs` | 解析并校验 HDB 目录、工作区、商品表/聚水潭资料路径 | 正常校验（只读） |
| `check_local_sources` | 加载处理模块，列出本地 HDB 与匹配的对账单列表 | 正常（只读，无文件不报错） |
| `download_tmcs_bill` | 缺源时经 Ops-Cli 下载 HDB 与对账单列表 | 跳过，不触发真实下载 |
| `download_promotion_bill` | 经 Ops-Cli 下载万象台/智多星推广账单 | 跳过 |
| `validate_sources` | 合并行、映射、构建各表、校验开票金额 | 本地无源则跳过 |
| `process_excel` | 组装并写出 `猫超{month}月账单数据表格.xlsx` | 跳过写文件，仅给出计划输出路径 |
| `collect_artifacts` | 汇总 HDB/对账单/推广账单/最终表为 Artifact | 记录可见产物 |

## Artifact

每个产物用 `core.runtime.Artifact` 记录，字段含 `type / role / name / path / platform / month / metadata`：

- HDB 源表：`role=hdb_source, platform=tmcs`
- 对账单列表：`role=statement_list`
- 推广账单：`role=promotion_source`
- 最终月账单：`role=output`

运行结束后产物汇总写入 `runtime/runs/YYYY-MM/run_xxx/artifacts.json`，每步记录写 `steps/<step_id>.json`，整体状态写 `run.json`。

## 边界

- workflow 层**不写**任何平台 URL / Cookie / Token / Selector / Playwright / CDP / SessionHub 逻辑。
- 平台动作通过 `clients/ops_cli_client.py` 调 `ops --json ...`，在同包 `billing.py` 内完成。
- 业务逻辑在同包 `billing.py` + `processor.py`（动态加载为 source 模块）+ `services/`；`tasks/tmall_monthly_bill/main.py` 仅为透传薄 wrapper。
- dry-run 不触发真实平台下载，也不写最终文件。
