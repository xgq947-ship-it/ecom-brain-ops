from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_zdx_fullsite_plan_create import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_zdx_fullsite_plan_create",
        "创建智多星全站推广计划",
        [
            step("check_inputs", "校验输入参数", steps.check_inputs),
            step("build_plan_name", "生成计划名称", steps.build_plan_name),
            step("resolve_target_roi", "获取目标投产比ROI", steps.resolve_target_roi),
            step("preview_plan_payload", "预览计划内容", steps.preview_plan_payload),
            step("create_zdx_plan", "调用Ops-Cli创建计划", steps.create_zdx_plan),
            step("collect_outputs", "收集结果与产物", steps.collect_outputs),
        ],
    )
