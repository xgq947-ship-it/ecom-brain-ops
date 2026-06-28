"""猫超营销端风险预警 workflow 的 step handler。

业务层只通过 clients.ops_cli_client.run_ops_json 调用 Ops-Cli，
不写猫超 URL、Cookie、Selector、Playwright、CDP。

dry-run 安全点：
- fetch 步骤向 Ops-Cli 透传 --dry-run，平台层返回 simulated=true，不请求真实猫超。
- workflow 只读取并输出数值，不处理/关闭任何预警，不做任何写入。
"""

from __future__ import annotations

import argparse
from typing import Any

from clients.ops_cli_client import run_ops_json
from core.runtime import parse_workflow_args, StepContext, failure_result, success_result


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags
    return success_result(outputs={"dry_run": flags.dry_run})


def fetch_risk_warning_count(ctx: StepContext):
    flags = ctx.state["flags"]
    command = ["--json", "tmcs", "marketing", "risk-warning", "count"]
    if flags.dry_run:
        command.append("--dry-run")
    try:
        payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        return failure_result(errors=[f"Ops-Cli 调用失败：{exc}"])

    data: dict[str, Any] = payload.get("data") or {}
    if "risk_warning_count" not in data:
        return failure_result(errors=[f"Ops-Cli 返回缺少 risk_warning_count 字段：{data}"])
    ctx.state["ops_data"] = data
    return success_result(
        outputs={
            "risk_warning_count": int(data.get("risk_warning_count", 0)),
            "label_text": data.get("label_text"),
            "source": data.get("source"),
            "simulated": bool(data.get("simulated", False)),
            "scene": data.get("scene"),
            "ops_context_path": data.get("context_path"),
        }
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    data = ctx.state.get("ops_data") or {}
    return success_result(
        outputs={
            "task": "tmcs_marketing_risk_warning",
            "dry_run": flags.dry_run,
            "risk_warning_count": int(data.get("risk_warning_count", 0)),
            "label_text": data.get("label_text"),
            "source": data.get("source"),
            "simulated": bool(data.get("simulated", False)),
            "scene": data.get("scene"),
            "ops_context_path": data.get("context_path"),
        }
    )
