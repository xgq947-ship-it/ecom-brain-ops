"""猫超营销端风险预警数值读取 workflow 定义。

只编排 3 个状态步骤：解析参数 -> 读取风险预警数值 -> 收集输出。
平台访问、scene、登录态恢复全部由 Ops-Cli `tmcs marketing risk-warning count` 完成。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_marketing_risk_warning import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_marketing_risk_warning",
        "猫超营销端风险预警数值读取",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("fetch_risk_warning_count", "读取风险预警数值", steps.fetch_risk_warning_count),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
