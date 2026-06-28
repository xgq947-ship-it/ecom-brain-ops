"""今日实时营业额 workflow 定义。"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.revenue_query import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "revenue_query",
        "今日实时营业额",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("fetch_order_stats", "查询订单营业额", steps.fetch_order_stats),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
