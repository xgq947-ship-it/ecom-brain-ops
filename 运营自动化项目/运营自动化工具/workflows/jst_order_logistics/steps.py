"""聚水潭订单物流轨迹查询 workflow 的 step handler。

编排层只负责：解析参数 -> 透传给 Ops-Cli 查询 -> 透出 ops-cli 同构结果。
不直连聚水潭、不解析平台 URL/Cookie/Selector，平台读取一律经
clients/ops_cli_client.py -> `ops jst order logistics`。

dry-run 安全点：
- 物流查询是平台读取动作，需要有效聚水潭登录态、可能触发短信验证，
  因此 dry-run 不发起真实查询，只回 skipped 预览，不消耗 session、不触发验证。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from core.config_loader import get_path
from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from clients.ops_cli_client import run_ops_json


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--order-id", action="append", default=None, help="聚水潭订单号，可重复")
    parser.add_argument("--outer-order-id", action="append", default=None, help="外部平台订单号，可重复")
    parser.add_argument("--input", dest="input_path", default=None, help="订单号输入文件 JSON/TXT/CSV")
    parser.add_argument("--limit", type=int, default=None, help="只查询前 N 个订单")
    parser.add_argument("--output", dest="output", default=None, help="结果写出路径（.json）；省略则只在结果里返回")
    parser.add_argument("--debug", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    order_ids = flags.order_id or []
    outer_order_ids = flags.outer_order_id or []
    if not order_ids and not outer_order_ids and not flags.input_path:
        return failure_result(
            errors=["请至少传入 --order-id、--outer-order-id 或 --input 之一"]
        )
    if flags.limit is not None and flags.limit <= 0:
        return failure_result(errors=["--limit 必须大于 0"])
    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "order_ids": order_ids,
            "outer_order_ids": outer_order_ids,
            "input_path": flags.input_path,
            "limit": flags.limit,
            "output": flags.output,
        }
    )


def fetch_logistics(ctx: StepContext):
    flags = ctx.state["flags"]

    if flags.dry_run:
        # 物流查询需有效登录态并可能触发短信验证，dry-run 一律不发起真实查询。
        ctx.state["payload"] = None
        return success_result(
            outputs={"skipped": True, "reason": "dry-run 跳过：物流查询不请求真实聚水潭"}
        )

    command = ["--json", "jst", "order", "logistics"]
    for value in flags.order_id or []:
        command += ["--order-id", str(value)]
    for value in flags.outer_order_id or []:
        command += ["--outer-order-id", str(value)]
    if flags.input_path:
        command += ["--input", str(flags.input_path)]
    if flags.limit is not None:
        command += ["--limit", str(flags.limit)]

    payload = run_ops_json(command, interactive_recovery=True)
    data = payload.get("data") or {}
    ctx.state["payload"] = payload

    summary = data.get("summary")
    if isinstance(summary, dict):
        outputs = {"summary": summary}
    else:
        # 单订单查询直接返回单条 item，没有 summary 字段。
        outputs = {
            "logistics_no": data.get("logistics_no"),
            "logistics_status": data.get("logistics_status"),
            "signed": data.get("signed"),
            "trace_count": len(data.get("trace_events") or []),
        }
    return success_result(outputs=outputs)


def write_output(ctx: StepContext):
    flags = ctx.state["flags"]
    payload = ctx.state.get("payload")

    if flags.dry_run or payload is None:
        return success_result(outputs={"skipped": True, "reason": "dry-run 或无查询结果，不写文件"})
    if not flags.output:
        return success_result(outputs={"written": False, "reason": "未指定 --output，仅在结果中返回"})

    output_path = Path(flags.output).expanduser()
    if not output_path.is_absolute():
        output_dir = Path(get_path("runtime_dir")) / "logistics"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / output_path.name
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    document = {
        "success": bool(payload.get("success")),
        "platform": payload.get("platform", "jst"),
        "command": payload.get("command", "order logistics"),
        "data": payload.get("data") or {},
        "queried_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ctx.add_artifact(
        Artifact(
            type="json",
            role="logistics_result",
            name=output_path.name,
            path=str(output_path),
            platform="jst",
        )
    )
    return success_result(outputs={"written": True, "output_path": str(output_path)})


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    payload = ctx.state.get("payload")

    if flags.dry_run or payload is None:
        return success_result(
            outputs={
                "task": "jst_order_logistics",
                "dry_run": flags.dry_run,
                "skipped": True,
                "reason": "dry-run 跳过：未发起真实物流查询",
            }
        )

    # 对齐 ops-cli 契约：透出 success / platform / command / data。
    return success_result(
        outputs={
            "task": "jst_order_logistics",
            "dry_run": False,
            "success": bool(payload.get("success")),
            "platform": payload.get("platform", "jst"),
            "command": payload.get("command", "order logistics"),
            "data": payload.get("data") or {},
        }
    )
