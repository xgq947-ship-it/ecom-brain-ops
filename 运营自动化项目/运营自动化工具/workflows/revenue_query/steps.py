"""今日实时营业额 workflow step handler。"""

from __future__ import annotations

import argparse
from typing import Any

from clients.ops_cli_client import run_ops_json
from core.runtime import StepContext, failure_result, parse_workflow_args, success_result


DEFAULT_DATE = "today"
DEFAULT_SHOP = "qiming"


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--date", default=DEFAULT_DATE)
    parser.add_argument("--shop", default=DEFAULT_SHOP)
    parser.add_argument("--dry-run", action="store_true")
    flags = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    flags.dry_run = ctx.dry_run or flags.dry_run
    return flags


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "date": flags.date,
            "shop": flags.shop,
            "dry_run": flags.dry_run,
        }
    )


def fetch_order_stats(ctx: StepContext):
    flags = ctx.state["flags"]
    command = ["--json", "jst", "order", "stats", "--date", flags.date, "--shop", flags.shop]
    ctx.state["ops_command"] = command
    if flags.dry_run:
        ctx.state["stats_data"] = {}
        return success_result(outputs={"planned": True, "ops_command": command})

    try:
        payload = run_ops_json(command, interactive_recovery=True)
    except RuntimeError as exc:
        return failure_result(errors=[f"Ops-Cli 调用失败：{exc}"], outputs={"ops_command": command})

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    ctx.state["stats_data"] = data
    return success_result(
        outputs={
            "date": data.get("date"),
            "shop": flags.shop,
            "store": data.get("store"),
            "order_count": data.get("order_count"),
            "paid_amount": data.get("paid_amount"),
            "metric_field": data.get("metric_field"),
            "ops_context_path": data.get("context_path"),
        }
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    data: dict[str, Any] = ctx.state.get("stats_data") or {}
    return success_result(
        outputs={
            "task": "revenue_query",
            "date": data.get("date") or flags.date,
            "shop": flags.shop,
            "store": data.get("store"),
            "order_count": data.get("order_count"),
            "paid_amount": data.get("paid_amount"),
            "metric_field": data.get("metric_field"),
            "planned": bool(flags.dry_run),
        }
    )
