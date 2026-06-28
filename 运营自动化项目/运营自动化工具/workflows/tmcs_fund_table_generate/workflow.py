from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_fund_table_generate import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_fund_table_generate",
        "猫超资金表生成",
        [
            step("check_inputs", "解析参数与准备目录", steps.check_inputs),
            step("fetch_receivable_amount", "获取待收货款", steps.fetch_receivable_amount),
            step("fetch_promotion_balance", "获取推广账户余额", steps.fetch_promotion_balance),
            step("validate_amounts", "校验金额与截图", steps.validate_amounts),
            step("generate_fund_table", "生成资金表", steps.generate_fund_table_step),
            step("verify_generated_excel", "校验生成表", steps.verify_generated_excel),
            step("collect_outputs", "收集产物", steps.collect_outputs),
        ],
    )
