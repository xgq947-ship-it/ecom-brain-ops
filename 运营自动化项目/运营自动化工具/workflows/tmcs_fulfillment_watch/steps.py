"""猫超物流履约监控 workflow 的 step handler。

业务层只通过 clients.ops_cli_client.run_ops_json 调用 Ops-Cli，
不写猫超 URL、Cookie、Selector、Playwright、CDP。

dry-run 安全点：
- fetch 步骤向 Ops-Cli 透传 --dry-run，平台层返回 simulated=true，不访问真实猫超。
- notify 步骤统一走 core.runtime.send_notification(dry_run=...)，dry-run 绝不真实发送。
- --simulate-risk 仅在 dry-run 下用本地风险样本覆盖指标，便于预览预警，不碰平台。
"""

from __future__ import annotations

import argparse
from typing import Any

from clients.ops_cli_client import run_ops_json
from core.runtime import parse_workflow_args, StepContext, failure_result, send_notification, success_result


DEFAULT_WARNING_MARGIN = 2.0

# 考核 / 观测指标：要求"达到或超过阈值"（按真实日常考核页口径）。
GE_THRESHOLDS: dict[str, float] = {
    "pickup_24h_rate": 95.0,
    "door_delivery_rate": 75.0,
    "next_day_delivery_rate": 55.0,
}

# 只记录、不自动预警的观测指标（平均支签时长、4CP 占比无明确达标线）。
RECORD_ONLY_KEYS: tuple[str, ...] = (
    "avg_pay_to_sign_hours",
    "four_cp_rate",
    "four_cp_rate_ex_remote",
)

METRIC_LABELS: dict[str, str] = {
    "pickup_24h_rate": "24H支揽率",
    "door_delivery_rate": "送货上门率",
    "next_day_delivery_rate": "隔日达率",
    "delivery_promise_rate": "表达签准率",
    "pickup_48h_rate": "48H支揽率",
    "four_cp_rate": "4CP占比",
    "four_cp_rate_ex_remote": "4CP占比_剔偏远",
    "avg_pay_to_sign_hours": "支签时长(小时)",
    "exception_feedback_required": "履约异常单反馈",
}

# --simulate-risk 用的本地风险样本（仅 dry-run 预览，不来自平台）。
SIMULATED_RISK_METRICS: dict[str, Any] = {
    "pickup_24h_rate": 93.0,        # 不合格
    "door_delivery_rate": 76.0,     # 接近预警
    "next_day_delivery_rate": 54.0, # 不合格
    "pickup_48h_rate": 99.5,        # 仅记录，不触发预警
    "delivery_promise_rate": 91.0,  # 仅记录，不触发预警
    "four_cp_rate": 88.0,           # 只记录
    "four_cp_rate_ex_remote": 88.0, # 只记录
    "avg_pay_to_sign_hours": 50.0,  # 只记录
    "exception_feedback_required": True,
}
SIMULATED_RISK_WEEKLY_LEVEL = "B"

DISPLAY_PRIORITY: dict[str, int] = {
    "pickup_24h_rate": 10,
    "door_delivery_rate": 20,
    "next_day_delivery_rate": 30,
    "weekly_warning_level": 60,
    "exception_feedback_required": 999,
}


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--warning-margin", type=float, default=DEFAULT_WARNING_MARGIN)
    parser.add_argument("--simulate-risk", action="store_true")
    parser.add_argument("--json", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    if flags.warning_margin < 0:
        return failure_result(errors=[f"warning-margin 必须为非负数，收到 {flags.warning_margin}"])
    if flags.simulate_risk and not flags.dry_run:
        return failure_result(errors=["--simulate-risk 仅允许在 dry-run 下用于预览预警。"])
    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "warning_margin": flags.warning_margin,
            "notify_requested": flags.notify,
            "simulate_risk": flags.simulate_risk,
        }
    )


def fetch_fulfillment_overview(ctx: StepContext):
    flags = ctx.state["flags"]
    command = ["--json", "tmcs", "fulfillment", "overview"]
    if flags.dry_run:
        command.append("--dry-run")
    try:
        payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        return failure_result(errors=[f"Ops-Cli 调用失败：{exc}"])

    data: dict[str, Any] = payload.get("data") or {}
    metrics = data.get("metrics")
    if not isinstance(metrics, dict):
        return failure_result(errors=[f"Ops-Cli 返回缺少 metrics 字段：{data}"])

    weekly_warning_level = data.get("weekly_warning_level")
    if flags.dry_run and flags.simulate_risk:
        metrics = dict(SIMULATED_RISK_METRICS)
        weekly_warning_level = SIMULATED_RISK_WEEKLY_LEVEL

    ctx.state["metrics"] = metrics
    ctx.state["weekly_warning_level"] = weekly_warning_level
    ctx.state["ops_data"] = data
    return success_result(
        outputs={
            "metrics": metrics,
            "weekly_warning_level": weekly_warning_level,
            "source": data.get("source"),
            "simulated": bool(data.get("simulated", False)),
            "scene": data.get("scene"),
            "ops_context_path": data.get("context_path"),
            "simulate_risk": flags.simulate_risk,
        }
    )


def evaluate_metrics(ctx: StepContext):
    flags = ctx.state["flags"]
    metrics: dict[str, Any] = ctx.state["metrics"]
    weekly_warning_level = ctx.state.get("weekly_warning_level")
    margin = float(flags.warning_margin)
    risk_items: list[dict[str, Any]] = []

    for key, threshold in GE_THRESHOLDS.items():
        if key not in metrics or metrics[key] is None:
            continue
        value = float(metrics[key])
        if value < threshold:
            severity = "fail"
        elif value <= threshold + margin:
            severity = "near"
        else:
            continue
        risk_items.append(
            {
                "metric": key,
                "label": METRIC_LABELS.get(key, key),
                "value": value,
                "threshold": threshold,
                "requirement": f">={threshold:g}",
                "severity": severity,
            }
        )

    if metrics.get("exception_feedback_required"):
        risk_items.append(
            {
                "metric": "exception_feedback_required",
                "label": METRIC_LABELS["exception_feedback_required"],
                "value": True,
                "threshold": None,
                "requirement": "当天有异常单需反馈",
                "severity": "action",
            }
        )

    if weekly_warning_level:
        risk_items.append(
            {
                "metric": "weekly_warning_level",
                "label": "周数据预警等级",
                "value": weekly_warning_level,
                "threshold": None,
                "requirement": "无预警",
                "severity": "weekly",
            }
        )

    visible_risk_items = [item for item in risk_items if item["metric"] != "exception_feedback_required"]
    should_notify = bool(visible_risk_items)
    ctx.state["risk_items"] = risk_items
    ctx.state["visible_risk_items"] = visible_risk_items
    ctx.state["should_notify"] = should_notify
    return success_result(
        outputs={
            "risk_items": risk_items,
            "visible_risk_items": visible_risk_items,
            "weekly_warning_level": weekly_warning_level,
            "should_notify": should_notify,
            "avg_pay_to_sign_hours": metrics.get("avg_pay_to_sign_hours"),
        }
    )


def build_warning_message(ctx: StepContext):
    risk_items = ctx.state.get("visible_risk_items") or []
    weekly_warning_level = ctx.state.get("weekly_warning_level")
    if not risk_items:
        ctx.state["warning_message"] = ""
        return success_result(outputs={"warning_message": "", "has_warning": False})

    sorted_items = sorted(
        risk_items,
        key=lambda item: DISPLAY_PRIORITY.get(item["metric"], 100),
    )
    lines = ["【猫超物流履约监控预警】"]
    if weekly_warning_level:
        lines.append(f"周数据预警等级：{weekly_warning_level} 类")
    for item in sorted_items:
        if item["metric"] == "weekly_warning_level":
            continue
        if item["severity"] == "fail":
            tag = "不合格"
        elif item["severity"] == "near":
            tag = "接近预警"
        else:
            tag = "风险"
        lines.append(f"- {item['label']}：{item['value']}（{tag}，要求 {item['requirement']}）")
    message = "\n".join(lines)
    ctx.state["warning_message"] = message
    return success_result(outputs={"warning_message": message, "has_warning": True})


def notify_if_needed(ctx: StepContext):
    flags = ctx.state["flags"]
    message = ctx.state.get("warning_message") or ""
    should_notify = ctx.state.get("should_notify", False)

    if not should_notify or not message:
        notification = {"sent": False, "reason": "无风险，默认不发送通知"}
        ctx.state["notification"] = notification
        return success_result(outputs={"notification": notification})

    if not flags.notify:
        notification = {"sent": False, "reason": "存在风险但未启用 --notify，仅记录预警", "preview": message}
        ctx.state["notification"] = notification
        return success_result(outputs={"notification": notification})

    notification = send_notification(message, dry_run=flags.dry_run, msgtype="markdown")
    ctx.state["notification"] = notification
    return success_result(outputs={"notification": notification})


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    data = ctx.state.get("ops_data") or {}
    return success_result(
        outputs={
            "task": "tmcs_fulfillment_watch",
            "dry_run": flags.dry_run,
            "metrics": ctx.state.get("metrics"),
            "risk_items": ctx.state.get("risk_items"),
            "warning_level": ctx.state.get("weekly_warning_level"),
            "should_notify": ctx.state.get("should_notify", False),
            "warning_message": ctx.state.get("warning_message") or "",
            "notification": ctx.state.get("notification"),
            "source": data.get("source"),
            "simulated": bool(data.get("simulated", False)),
            "scene": data.get("scene"),
            "ops_context_path": data.get("context_path"),
        }
    )
