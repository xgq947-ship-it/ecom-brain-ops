"""聚水潭店铺利润快照 workflow 定义。"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_shop_profit_snapshot import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_shop_profit_snapshot",
        "聚水潭店铺利润快照",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("fetch_profit_detail", "拉取利润明细", steps.fetch_profit_detail),
            step("write_snapshot", "写出快照", steps.write_snapshot),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )

