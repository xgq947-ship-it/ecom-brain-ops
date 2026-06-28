"""聚水潭订单换货补发 workflow 的 step handler。

编排层只负责把 `ops jst order exchange-resend`（learn / preview / submit）拆成有状态步骤，
不重写任何订单 / 换货 / 补发逻辑，也不直接请求平台（仍经 clients/ops_cli_client.py -> Ops-Cli）。
本文件不感知平台请求细节。

dry-run / 安全点：
- inspect_existing_capabilities / learn_or_preview_flow 只读（不含 --execute）；dry-run 下平台不可达则 skip。
- submit_if_execute 在 dry-run、未传 --execute、或 --learn-only 时一律跳过，绝不调用含 --execute 的命令。
- 真实提交前会先输出 final_payload；找不到订单 / 状态不允许 / 商品不匹配时停止。
"""

from __future__ import annotations

import argparse

from core.runtime import parse_workflow_args, StepContext, failure_result, success_result

from clients.ops_cli_client import run_ops_json

_VALID_MODES = ("resend", "exchange")
_MODE_LABELS = {"resend": "补发", "exchange": "换货"}


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--order-no", dest="order_no", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--reason", default=None)
    parser.add_argument("--remark", default=None)
    parser.add_argument("--sku-code", dest="sku_code", default=None)
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--confirm-order-no", dest="confirm_order_no", default=None)
    parser.add_argument("--screenshot-dir", dest="screenshot_dir", default=None)
    parser.add_argument("--learn-only", dest="learn_only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def _common_opts(flags: argparse.Namespace) -> list[str]:
    opts = ["--order-no", flags.order_no, "--mode", flags.mode]
    if flags.reason:
        opts += ["--reason", flags.reason]
    if flags.remark:
        opts += ["--remark", flags.remark]
    if flags.sku_code:
        opts += ["--sku-code", flags.sku_code]
    opts += ["--qty", str(flags.qty)]
    return opts


def _preview_cmd(flags: argparse.Namespace) -> list[str]:
    return ["jst", "order", "exchange-resend", "preview", *_common_opts(flags)]


def _learn_cmd(flags: argparse.Namespace) -> list[str]:
    cmd = ["jst", "order", "exchange-resend", "learn", *_common_opts(flags)]
    if flags.screenshot_dir:
        cmd += ["--screenshot-dir", flags.screenshot_dir]
    return cmd


def _submit_cmd(flags: argparse.Namespace) -> list[str]:
    return [
        "jst",
        "order",
        "exchange-resend",
        "submit",
        *_common_opts(flags),
        "--execute",
        "--confirm-order-no",
        flags.confirm_order_no,
    ]


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags

    if not flags.order_no:
        return failure_result("缺少订单号：请传入 --order-no")
    if flags.mode not in _VALID_MODES:
        return failure_result("--mode 仅支持 resend（补发）或 exchange（换货）")
    if flags.qty is not None and flags.qty <= 0:
        return failure_result("--qty 必须大于 0")
    if flags.execute:
        if flags.learn_only:
            return failure_result("--learn-only 与 --execute 互斥：探索阶段不允许提交")
        if not flags.confirm_order_no:
            return failure_result("真实提交必须传入 --confirm-order-no 二次确认")
        if str(flags.confirm_order_no).strip() != str(flags.order_no).strip():
            return failure_result("--confirm-order-no 与 --order-no 不一致，已拒绝提交")

    return success_result(
        outputs={
            "order_no": flags.order_no,
            "mode": flags.mode,
            "mode_label": _MODE_LABELS[flags.mode],
            "dry_run": flags.dry_run,
            "execute": flags.execute,
            "learn_only": flags.learn_only,
            "sku_code": flags.sku_code,
            "qty": flags.qty,
        }
    )


def inspect_existing_capabilities(ctx: StepContext):
    """复用已有订单查询能力（只读 preview）解析订单与资格。"""
    flags = ctx.state["flags"]
    try:
        payload = run_ops_json(_preview_cmd(flags), interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        if flags.dry_run:
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))

    data = payload.get("data") or {}
    ctx.state["preview"] = data
    return success_result(
        outputs={
            "found_order": data.get("found_order"),
            "matched_filter": data.get("matched_filter"),
            "order_status": data.get("order_status"),
            "eligible": data.get("eligible"),
            "ineligible_reason": data.get("ineligible_reason"),
            "sku_matched": data.get("sku_matched"),
        }
    )


def learn_or_preview_flow(ctx: StepContext):
    """--learn-only 时调用 Ops-Cli 学习换货/补发入口；否则沿用上一步 preview 结果。"""
    flags = ctx.state["flags"]
    if not flags.learn_only:
        return success_result(
            outputs={"skipped": True, "reason": "preview 已在 inspect_existing_capabilities 完成"}
        )

    try:
        payload = run_ops_json(_learn_cmd(flags), interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        if flags.dry_run:
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))

    data = payload.get("data") or {}
    ctx.state["learn"] = data
    return success_result(
        outputs={
            "steps_detected": data.get("steps_detected"),
            "screenshot_paths": data.get("screenshot_paths"),
            "profile_path": data.get("profile_path"),
            "found_order": data.get("found_order"),
            "eligible": data.get("eligible"),
        }
    )


def validate_eligibility(ctx: StepContext):
    """找不到订单 / 状态不允许 / 商品不匹配时必须停止。learn-only 只探索不强制。"""
    flags = ctx.state["flags"]
    if flags.learn_only:
        return success_result(outputs={"skipped": True, "reason": "learn-only 仅探索，不校验资格"})

    preview = ctx.state.get("preview")
    if preview is None:
        if flags.dry_run:
            return success_result(outputs={"skipped": True, "reason": "dry-run 下平台不可达，跳过资格校验"})
        return failure_result("未获取到订单预览数据，无法校验资格")

    if not preview.get("found_order"):
        return failure_result(f"未找到订单 {flags.order_no}，停止")
    if not preview.get("eligible"):
        reason = preview.get("ineligible_reason") or "订单不满足换货 / 补发条件"
        return failure_result(f"订单不可{_MODE_LABELS[flags.mode]}：{reason}")

    return success_result(
        outputs={
            "found_order": True,
            "eligible": True,
            "order_status": preview.get("order_status"),
            "sku_matched": preview.get("sku_matched"),
            "final_payload": preview.get("final_payload"),
        }
    )


def submit_if_execute(ctx: StepContext):
    flags = ctx.state["flags"]
    if flags.learn_only:
        return success_result(outputs={"skipped": True, "reason": "learn-only 不提交"})
    if flags.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run 不提交"})
    if not flags.execute:
        return success_result(outputs={"skipped": True, "reason": "未指定 --execute，预览已完成"})

    try:
        payload = run_ops_json(_submit_cmd(flags), interactive_recovery=True)
    except RuntimeError as exc:
        return failure_result(f"提交换货 / 补发失败：{exc}")

    data = payload.get("data") or {}
    ctx.state["submit"] = data
    return success_result(
        outputs={
            "submitted": data.get("submitted"),
            "final_payload": data.get("final_payload"),
            "pending_confirmation": data.get("pending_confirmation"),
            "order_status": data.get("order_status"),
            "eligible": data.get("eligible"),
        }
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    preview = ctx.state.get("preview") or {}
    learn = ctx.state.get("learn") or {}
    submit = ctx.state.get("submit") or {}
    return success_result(
        outputs={
            "task": "jst_order_exchange_resend",
            "order_no": flags.order_no,
            "mode": flags.mode,
            "mode_label": _MODE_LABELS[flags.mode],
            "dry_run": flags.dry_run,
            "execute": flags.execute,
            "learn_only": flags.learn_only,
            "found_order": submit.get("found_order", preview.get("found_order")),
            "order_status": submit.get("order_status") or preview.get("order_status"),
            "eligible": submit.get("eligible", preview.get("eligible")),
            "sku_matched": preview.get("sku_matched"),
            "submitted": submit.get("submitted", False),
            "final_payload": submit.get("final_payload") or preview.get("final_payload") or {},
            "pending_confirmation": submit.get("pending_confirmation") or preview.get("pending_confirmation") or [],
            "steps_detected": learn.get("steps_detected") or [],
            "screenshot_paths": learn.get("screenshot_paths") or [],
        }
    )
