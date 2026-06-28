"""猫超 XP 工单监控 workflow 的 step handler。

业务层只通过 clients.ops_cli_client.run_ops_json 调用 Ops-Cli，
不写猫超 URL、Cookie、Selector、Playwright、CDP。

dry-run 安全点：
- fetch 步骤向 Ops-Cli 透传 --dry-run，平台层返回 simulated=true，不请求真实猫超。
- 通知统一由外层 watchdog wrapper 处理；workflow 只返回通知建议，不真实发送。
"""

from __future__ import annotations

import argparse
from typing import Any

from clients.ops_cli_client import run_ops_json
from core.runtime import parse_workflow_args, StepContext, failure_result, success_result


DEFAULT_THRESHOLD = 4


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--json", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    if flags.threshold < 0:
        return failure_result(errors=[f"threshold 必须为非负整数，收到 {flags.threshold}"])
    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "threshold": flags.threshold,
            "notify_requested": flags.notify,
        }
    )


def fetch_workorder_count(ctx: StepContext):
    flags = ctx.state["flags"]
    command = [
        "--json",
        "tmcs",
        "xp-workorder",
        "count",
        "--threshold",
        str(flags.threshold),
    ]
    if flags.dry_run:
        command.append("--dry-run")
    try:
        payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        return failure_result(errors=[f"Ops-Cli 调用失败：{exc}"])

    data: dict[str, Any] = payload.get("data") or {}
    if "count" not in data:
        return failure_result(errors=[f"Ops-Cli 返回缺少 count 字段：{data}"])
    ctx.state["ops_data"] = data
    return success_result(
        outputs={
            "count": int(data.get("count", 0)),
            "source": data.get("source"),
            "simulated": bool(data.get("simulated", False)),
            "scene": data.get("scene"),
            "ops_context_path": data.get("context_path"),
        }
    )


def evaluate_threshold(ctx: StepContext):
    from datetime import datetime

    flags = ctx.state["flags"]
    data = ctx.state["ops_data"]
    count = int(data.get("count", 0))
    threshold = int(data.get("threshold", flags.threshold))
    exceeded = count > threshold
    now_str = datetime.now().strftime("%m-%d %H:%M")

    if exceeded:
        pending = count - threshold
        message = (
            f"## 🔔 猫超 XP 工单提醒\n\n"
            f"> 检查时间：{now_str}\n\n"
            f"待处理工单：**{pending}**"
        )
    else:
        message = f"当前猫超 XP 工单数量：{count}"

    ctx.state["count"] = count
    ctx.state["threshold"] = threshold
    ctx.state["exceeded"] = exceeded
    ctx.state["message"] = message
    return success_result(
        outputs={
            "count": count,
            "threshold": threshold,
            "exceeded": exceeded,
            "message": message,
        }
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    data = ctx.state.get("ops_data") or {}
    exceeded = ctx.state.get("exceeded", False)
    message = ctx.state.get("message", "")

    if flags.notify and exceeded and message:
        notification = {
            "sent": False,
            "reason": "通知由外层 watchdog 发送，workflow 仅返回提醒内容",
            "delegate": "watchdog_wrapper",
        }
    elif flags.notify and not exceeded:
        notification = {"sent": False, "reason": "工单数未超过阈值，无需通知", "delegate": "watchdog_wrapper"}
    elif not flags.notify:
        notification = {"sent": False, "reason": "通知未启用"}
    else:
        notification = {"sent": False, "reason": "无通知内容"}

    return success_result(
        outputs={
            "task": "tmcs_xp_workorder_watch",
            "dry_run": flags.dry_run,
            "count": ctx.state.get("count"),
            "threshold": ctx.state.get("threshold"),
            "exceeded": exceeded,
            "message": message,
            "source": data.get("source"),
            "simulated": bool(data.get("simulated", False)),
            "scene": data.get("scene"),
            "ops_context_path": data.get("context_path"),
            "notification": notification,
        }
    )
