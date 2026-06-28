"""猫超物流履约监控 workflow 定义。

属"平台读取 + workflow 业务判断"类型：
- 平台读取（首页 → 天机 → 商家仓履约 → 日常考核 → 数据概览）全部由
  Ops-Cli `tmcs fulfillment overview` 完成，本层不碰 URL/Cookie/Token/Selector。
- 本层只负责：考核/观测指标判断、接近预警判断、周数据预警等级透传、通知预览。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_fulfillment_watch import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_fulfillment_watch",
        "猫超物流履约监控",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("fetch_fulfillment_overview", "拉取履约数据概览", steps.fetch_fulfillment_overview),
            step("evaluate_metrics", "判断指标与接近预警", steps.evaluate_metrics),
            step("build_warning_message", "生成预警信息", steps.build_warning_message),
            step("notify_if_needed", "按需通知（预览）", steps.notify_if_needed),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
