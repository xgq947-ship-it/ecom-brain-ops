"""聚水潭刷单报销工单 workflow 定义。

拆成 7 个有状态步骤；批次读取/候选核验/报销调用/标记行补丁等业务逻辑在同包 reimburse.py。
旧命令 `python3 run.py 刷单报销登记 ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_brush_reimburse_workorder import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_brush_reimburse_workorder",
        "聚水潭刷单报销工单",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("load_reimburse_data", "读取当前批次", steps.load_reimburse_data),
            step("validate_amounts", "校验金额合计", steps.validate_amounts),
            step("preview_workorder", "核验候选工单(只读)", steps.preview_workorder),
            step("submit_workorder", "提交报销工单", steps.submit_workorder),
            step("update_register", "回写登记表标记", steps.update_register),
            step("collect_artifacts", "收集产物", steps.collect_artifacts),
        ],
    )
