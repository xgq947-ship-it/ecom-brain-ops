"""猫超月账单整理 workflow 定义。

拆成 7 个有状态步骤；账单解析/Excel 加工/平台下载等业务逻辑在同包 billing.py +
processor.py + services/，本 workflow 只负责步骤编排与运行记录。旧命令
`python3 run.py 猫超账单整理` 不受影响，走 tasks/tmall_monthly_bill/main.py 薄 wrapper。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmall_monthly_bill import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmall_monthly_bill",
        "猫超月账单整理",
        [
            step("check_inputs", "检查输入与路径", steps.check_inputs),
            step("check_local_sources", "检查本地数据源", steps.check_local_sources),
            step("download_tmcs_bill", "下载猫超账单", steps.download_tmcs_bill),
            step("download_promotion_bill", "下载推广账单", steps.download_promotion_bill),
            step("validate_sources", "校验并构建账单数据", steps.validate_sources),
            step("process_excel", "生成月账单 Excel", steps.process_excel),
            step("collect_artifacts", "收集产物", steps.collect_artifacts),
        ],
    )
