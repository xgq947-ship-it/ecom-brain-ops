"""聚水潭订单换货补发 workflow 定义。

封装 `ops jst order exchange-resend` CLI（learn / preview / submit），拆成 6 个有状态步骤。
平台交互完全由 Ops-Cli 持有，
业务层只通过 clients/ops_cli_client.py 调用，不重写任何订单 / 换货 / 补发逻辑，
也不感知平台请求细节。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_order_exchange_resend import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_order_exchange_resend",
        "聚水潭订单换货补发",
        [
            step("check_inputs", "解析并校验参数", steps.check_inputs),
            step("inspect_existing_capabilities", "复用已有订单查询能力(只读)", steps.inspect_existing_capabilities),
            step("learn_or_preview_flow", "调用 Ops-Cli 探索/预览换货补发入口", steps.learn_or_preview_flow),
            step("validate_eligibility", "确认订单存在、状态允许、商品匹配", steps.validate_eligibility),
            step("submit_if_execute", "仅 --execute 且确认订单号一致时提交", steps.submit_if_execute),
            step("collect_outputs", "汇总结果", steps.collect_outputs),
        ],
    )
