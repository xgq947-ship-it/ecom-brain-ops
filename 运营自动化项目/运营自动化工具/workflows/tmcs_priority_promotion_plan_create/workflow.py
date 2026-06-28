from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_priority_promotion_plan_create import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_priority_promotion_plan_create",
        "猫超优先推广自动建计划",
        [
            step("check_inputs", "校验输入参数", steps.check_inputs),
            step("load_priority_promotion_list", "读取优先推广清单", steps.load_priority_promotion_list),
            step("load_active_promotion_list", "读取正在推广商品列表", steps.load_active_promotion_list),
            step("filter_not_active", "过滤已在推广商品", steps.filter_not_active),
            step("resolve_item_ids_for_plan", "解析商品ID", steps.resolve_item_ids_for_plan),
            step("build_create_plan_payloads", "构造建计划参数", steps.build_create_plan_payloads),
            step("create_zdx_plans", "逐个调用智多星建计划workflow", steps.create_zdx_plans),
            step("sync_active_promotion_list", "回写正在推广商品列表", steps.sync_active_promotion_list),
            step("write_outputs", "写出筛选结果", steps.write_outputs),
            step("collect_outputs", "汇总结果", steps.collect_outputs),
        ],
    )
