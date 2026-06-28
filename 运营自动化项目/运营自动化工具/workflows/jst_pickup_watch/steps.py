"""聚水潭揽收监控 workflow 的 step handler。

风险评估、提醒文案等纯业务逻辑在同包 risk_evaluator.py；平台读取经
clients/ops_cli_client.py -> Ops-Cli；通知经 core.runtime.notify。
本层只负责把这些能力拆成有状态步骤，不写平台 URL/Cookie/Token/Selector。

dry-run 安全点：
- fetch 步骤向 Ops-Cli 透传 --dry-run，平台层用模拟订单，不请求真实聚水潭。
- notify 步骤在 dry-run 下只产出 preview，绝不发送真实微信。
"""

from __future__ import annotations

import argparse
from datetime import datetime

from core.runtime import parse_workflow_args, StepContext, failure_result, success_result
from core.runtime.notify import send_notification

from clients.ops_cli_client import run_ops_json
from workflows.jst_pickup_watch import risk_evaluator


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hours", type=int, default=None)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--notify", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "notify": flags.notify,
            "debug": flags.debug,
            "hours_requested": flags.hours,
        }
    )


def load_config(ctx: StepContext):
    flags = ctx.state["flags"]
    config = risk_evaluator.load_config()
    hours = flags.hours or int(config["pickup_watch"]["hours"])
    ctx.state["config"] = config
    ctx.state["hours"] = hours
    return success_result(outputs={"hours": hours})


def fetch_pickup_watch_data(ctx: StepContext):
    flags = ctx.state["flags"]
    hours = ctx.state["hours"]
    command = ["--json", "jst", "order", "pickup-watch", "--hours", str(hours), "--output", "json"]
    if flags.dry_run:
        command.append("--dry-run")
    if flags.debug:
        command.append("--debug")
    payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    data = payload.get("data") or {}
    orders = list(data.get("orders") or [])
    checked_at = str(data.get("checked_at") or datetime.now().astimezone().isoformat(timespec="seconds"))
    ctx.state["orders"] = orders
    ctx.state["checked_at"] = checked_at
    return success_result(outputs={"checked_at": checked_at, "order_count": len(orders)})


def analyze_abnormal_orders(ctx: StepContext):
    config = ctx.state["config"]
    orders = ctx.state["orders"]
    checked_at = ctx.state["checked_at"]
    check_time = risk_evaluator._parse_datetime(checked_at, datetime.now().astimezone())
    abnormal, counts = risk_evaluator.evaluate_orders(orders, config, now=check_time)
    content = risk_evaluator.build_notification_content(counts=counts, rows=abnormal)
    ctx.state["abnormal"] = abnormal
    ctx.state["counts"] = counts
    ctx.state["content"] = content
    return success_result(
        outputs={
            "summary": counts,
            "abnormal_order_nos": [
                item.get("platform_order_no") or item.get("jst_order_no") for item in abnormal
            ],
        }
    )


def notify_if_needed(ctx: StepContext):
    flags = ctx.state["flags"]
    abnormal = ctx.state["abnormal"]
    content = ctx.state["content"]

    if not abnormal:
        notification = {"success": True, "sent": False, "reason": "无异常订单，不发送微信"}
    elif flags.dry_run:
        # 统一通知入口在 dry-run 下只产预览、绝不发送真实微信。
        notification = send_notification(content, dry_run=True, msgtype="markdown")
    elif flags.notify:
        notification = send_notification(content, dry_run=False, msgtype="markdown")
    else:
        notification = {"success": True, "sent": False, "reason": "通知未启用"}

    ctx.state["notification"] = notification
    return success_result(outputs={"notification": notification})


def collect_outputs(ctx: StepContext):
    counts = ctx.state.get("counts", {})
    abnormal = ctx.state.get("abnormal", [])
    return success_result(
        outputs={
            "task": "jst_pickup_watch",
            "dry_run": ctx.state["flags"].dry_run,
            "hours": ctx.state.get("hours"),
            "checked_at": ctx.state.get("checked_at"),
            "summary": counts,
            "abnormal_order_nos": [
                item.get("platform_order_no") or item.get("jst_order_no") for item in abnormal
            ],
            "notification": ctx.state.get("notification"),
        }
    )
