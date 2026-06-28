"""聚水潭发票工单 workflow 的 step handler。

编排层只负责把 `ops jst order invoice` CLI 拆成有状态步骤，不重写任何发票或订单逻辑，
也不直接请求平台（仍经 clients/ops_cli_client.py -> Ops-Cli）。

dry-run 安全点：
- resolve_order 调用 ops（不含 --execute），为只读查询；干跑时 interactive_recovery=False，
  若平台不可达则 skip 而非报错。
- submit_workorder 在 dry-run 下直接跳过，绝不调用含 --execute 的命令。
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation

from core.runtime import parse_workflow_args, StepContext, failure_result, success_result

from clients.ops_cli_client import run_ops_json

_REQUIRED_INVOICE_FIELDS = ("shop_name", "invoice_entity", "title", "tax_no", "address", "phone", "bank", "bank_account", "amount")


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--order-id", dest="order_id", default=None)
    parser.add_argument("--outer-order-id", dest="outer_order_id", default=None)
    parser.add_argument("--shop-name", dest="shop_name", default=None)
    parser.add_argument("--invoice-entity", dest="invoice_entity", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--tax-no", dest="tax_no", default=None)
    parser.add_argument("--address", default=None)
    parser.add_argument("--phone", default=None)
    parser.add_argument("--bank", default=None)
    parser.add_argument("--bank-account", dest="bank_account", default=None)
    parser.add_argument("--amount", default=None)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--invoice-type", dest="invoice_type", default="专用发票")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def _build_command(flags: argparse.Namespace, *, execute: bool = False) -> list[str]:
    cmd: list[str] = ["jst", "order", "invoice"]
    if flags.order_id:
        cmd += ["--order-id", flags.order_id]
    elif flags.outer_order_id:
        cmd += ["--outer-order-id", flags.outer_order_id]
    if flags.shop_name:
        cmd += ["--shop-name", flags.shop_name]
    if flags.invoice_entity:
        cmd += ["--invoice-entity", flags.invoice_entity]
    cmd += [
        "--title", flags.title,
        "--tax-no", flags.tax_no,
        "--address", flags.address,
        "--phone", flags.phone,
        "--bank", flags.bank,
        "--bank-account", flags.bank_account,
        "--amount", str(flags.amount),
        "--quantity", str(flags.quantity),
        "--invoice-type", flags.invoice_type,
    ]
    if execute:
        cmd.append("--execute")
    return cmd


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags

    if not flags.order_id and not flags.outer_order_id:
        return failure_result("缺少订单号：请传入 --order-id 或 --outer-order-id")

    missing = [f for f in _REQUIRED_INVOICE_FIELDS if not getattr(flags, f, None)]
    if missing:
        return failure_result(f"缺少必填发票字段：{', '.join(missing)}")

    try:
        amount = Decimal(str(flags.amount))
        if amount <= 0:
            return failure_result("--amount 必须大于 0")
    except InvalidOperation:
        return failure_result(f"--amount 不是合法金额：{flags.amount!r}")

    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "execute": flags.execute,
            "order_id": flags.order_id,
            "outer_order_id": flags.outer_order_id,
            "shop_name": flags.shop_name,
            "invoice_entity": flags.invoice_entity,
            "title": flags.title,
            "tax_no": flags.tax_no,
            "amount": str(flags.amount),
            "invoice_type": flags.invoice_type,
            "quantity": flags.quantity,
        }
    )


def resolve_order(ctx: StepContext):
    flags = ctx.state["flags"]
    cmd = _build_command(flags, execute=False)
    try:
        payload = run_ops_json(cmd, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        if flags.dry_run:
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))

    data = payload.get("data") or {}
    ctx.state["resolved"] = data
    return success_result(
        outputs={
            "order_id": data.get("order_id"),
            "internal_order_id": data.get("internal_order_id"),
            "online_order_id": data.get("online_order_id"),
            "matched_filter": data.get("matched_filter"),
            "invoice_type": data.get("invoice_type"),
            "amount": data.get("amount"),
            "submitted": data.get("submitted"),
        }
    )


def submit_workorder(ctx: StepContext):
    flags = ctx.state["flags"]
    if flags.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run 不提交真实工单"})
    if not flags.execute:
        return success_result(outputs={"skipped": True, "reason": "未指定 --execute，预览已完成"})

    cmd = _build_command(flags, execute=True)
    try:
        payload = run_ops_json(cmd, interactive_recovery=True)
    except RuntimeError as exc:
        return failure_result(f"提交发票工单失败：{exc}")

    data = payload.get("data") or {}
    ctx.state["submit_result"] = data
    return success_result(
        outputs={
            "submitted": data.get("submitted"),
            "internal_order_id": data.get("internal_order_id"),
            "online_order_id": data.get("online_order_id"),
            "result": data.get("result"),
        }
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    resolved = ctx.state.get("resolved") or {}
    submit_result = ctx.state.get("submit_result") or {}
    return success_result(
        outputs={
            "task": "jst_order_invoice_workorder",
            "dry_run": flags.dry_run,
            "execute": flags.execute,
            "order_id": flags.order_id or flags.outer_order_id,
            "internal_order_id": resolved.get("internal_order_id") or submit_result.get("internal_order_id"),
            "online_order_id": resolved.get("online_order_id") or submit_result.get("online_order_id"),
            "invoice_type": flags.invoice_type,
            "shop_name": flags.shop_name,
            "invoice_entity": flags.invoice_entity,
            "title": flags.title,
            "tax_no": flags.tax_no,
            "amount": str(flags.amount),
            "submitted": submit_result.get("submitted", False),
            "result": submit_result.get("result"),
        }
    )
