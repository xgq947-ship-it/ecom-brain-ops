"""猫超 XP 工单数量监控 workflow 定义。

只编排 4 个状态步骤：解析参数 -> 拉取数量 -> 比较阈值 -> 收集输出 + 通知。
平台访问、scene、登录态恢复全部由 Ops-Cli `tmcs xp-workorder count` 完成。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_xp_workorder_watch import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_xp_workorder_watch",
        "猫超 XP 工单数量监控",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("fetch_workorder_count", "拉取 XP 工单数量", steps.fetch_workorder_count),
            step("evaluate_threshold", "判断是否超过阈值", steps.evaluate_threshold),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
