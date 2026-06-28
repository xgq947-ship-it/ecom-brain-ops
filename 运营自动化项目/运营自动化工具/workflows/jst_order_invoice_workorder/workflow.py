"""聚水潭发票工单 workflow 定义。

封装 `ops jst order invoice` CLI，拆成 4 个有状态步骤；平台逻辑完全由 Ops-Cli 持有，
业务层只通过 clients/ops_cli_client.py 调用，不重写任何发票或订单逻辑。
旧命令（若有）不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_order_invoice_workorder import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_order_invoice_workorder",
        "聚水潭发票工单",
        [
            step("check_inputs", "解析并校验参数", steps.check_inputs),
            step("resolve_order", "解析订单并构建工单预览(只读)", steps.resolve_order),
            step("submit_workorder", "提交发票工单", steps.submit_workorder),
            step("collect_outputs", "汇总结果", steps.collect_outputs),
        ],
    )
