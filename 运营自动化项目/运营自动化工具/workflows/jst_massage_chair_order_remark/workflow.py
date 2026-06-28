from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_massage_chair_order_remark import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_massage_chair_order_remark",
        "聚水潭按摩椅订单自动备注",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("fetch_orders", "查询聚水潭订单", steps.fetch_orders),
            step("load_massage_chair_mapping", "读取按摩椅资料表", steps.load_massage_chair_mapping),
            step("build_remark_plan", "生成备注计划", steps.build_remark_plan),
            step("apply_remarks", "执行备注", steps.apply_remarks),
            step("normalize_abnormal_orders", "异常单转正常", steps.normalize_abnormal_orders),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
