"""聚水潭刷单订单打标 workflow 定义。

把 tasks/jst_order_label 拆成 5 个有状态步骤；真实逻辑全部复用 legacy 的平台调用。
旧命令 `python3 run.py 刷单订单插黄旗 ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_order_label import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_order_label",
        "聚水潭刷单订单打标",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("load_orders", "确定订单来源", steps.load_orders),
            step("preview_labels", "预览打标(只查询)", steps.preview_labels),
            step("apply_labels", "执行打标", steps.apply_labels),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
