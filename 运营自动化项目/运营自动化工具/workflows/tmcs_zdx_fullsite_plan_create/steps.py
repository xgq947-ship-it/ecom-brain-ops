"""智多星货品全站推广计划创建 workflow 的 step handler。

业务层只通过 clients.ops_cli_client.run_ops_json 调用 Ops-Cli，
不写猫超 URL、Cookie、Selector、Playwright、CDP。

ROI 来源：
- 用户传 --roi → 直接使用
- 否则直接调用 workflows.tmcs_sku_roi 的 excel_lookup + roi_calculator，
  用 item_id 作为 product_code 查询（商品ID = 商品编码）

dry-run 安全点：
- create_zdx_plan 步骤向 Ops-Cli 传 --dry-run，不传 --execute
- --execute 缺失时 check_inputs 直接失败
- confirm_plan_name 不匹配时 check_inputs 直接失败
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any

from clients.ops_cli_client import run_ops_json
from core.config_loader import get_path
from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.tmcs_sku_roi.excel_lookup import (
    find_jst_product,
    find_tmcs_barcode,
    load_roi_config,
)
from workflows.tmcs_sku_roi.roi_calculator import calculate_roi as _calculate_roi


_DEFAULT_ROI_CONFIG_FILE = get_path("project_root") / "config" / "tmcs_sku_roi.json"
_DEFAULT_TMCS_FILE = get_path("tmall_goods_master_file")
_DEFAULT_JST_FILE = get_path("jst_product_master_file")


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--item-id", required=False, default=None)
    parser.add_argument("--daily-budget", type=float, required=False, default=None)
    parser.add_argument("--plan-name", required=False, default=None)
    parser.add_argument("--roi", type=float, required=False, default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-plan-name", required=False, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tmcs-file", default=str(_DEFAULT_TMCS_FILE))
    parser.add_argument("--jst-file", default=str(_DEFAULT_JST_FILE))
    parser.add_argument("--roi-config", default=str(_DEFAULT_ROI_CONFIG_FILE))
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)

    if not flags.item_id:
        return failure_result(errors=["--item-id 必填，请提供天猫商品ID（商品编码）。"])

    if flags.daily_budget is None:
        return failure_result(errors=["--daily-budget 必填，请提供每日预算金额。"])

    if flags.daily_budget <= 0:
        return failure_result(errors=[f"--daily-budget 必须大于 0，当前值：{flags.daily_budget}。"])

    if not flags.dry_run:
        if not flags.execute:
            return failure_result(
                errors=[
                    "真实创建计划必须显式传入 --execute。"
                    "如需预览请使用 --dry-run。"
                ]
            )
        if not flags.confirm_plan_name:
            return failure_result(
                errors=[
                    "真实执行时必须传入 --confirm-plan-name 以确认计划名称，"
                    "请先用 --dry-run 查看计划名称后再执行。"
                ]
            )

    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "item_id": flags.item_id,
            "daily_budget": flags.daily_budget,
            "dry_run": flags.dry_run,
            "execute": flags.execute,
        }
    )


def build_plan_name(ctx: StepContext):
    flags = ctx.state["flags"]
    if flags.plan_name:
        plan_name = flags.plan_name
    else:
        today = date.today()
        mmdd = today.strftime("%m%d")
        plan_name = f"全站推广_{flags.item_id}_{mmdd}"

    # 真实执行时校验 confirm_plan_name 必须与生成的 plan_name 一致
    if not flags.dry_run and flags.execute:
        if flags.confirm_plan_name != plan_name:
            return failure_result(
                errors=[
                    f"--confirm-plan-name 与计划名称不匹配。"
                    f"期望：{plan_name!r}，收到：{flags.confirm_plan_name!r}。"
                    "请重新运行 --dry-run 查看最终计划名称后再执行。"
                ]
            )

    ctx.state["plan_name"] = plan_name
    return success_result(outputs={"plan_name": plan_name})


def resolve_target_roi(ctx: StepContext):
    flags = ctx.state["flags"]

    if flags.roi is not None:
        if flags.roi <= 0:
            return failure_result(errors=[f"--roi 必须大于 0，当前值：{flags.roi}。"])
        ctx.state["target_roi"] = flags.roi
        ctx.state["roi_source"] = "manual"
        return success_result(outputs={"target_roi": flags.roi, "roi_source": "manual"})

    # 从 Excel 查询 ROI（直接调用 tmcs_sku_roi 的 excel_lookup + roi_calculator，不走子进程）
    tmcs_file = Path(flags.tmcs_file).expanduser()
    jst_file = Path(flags.jst_file).expanduser()
    roi_config_file = Path(flags.roi_config).expanduser()

    missing = [str(p) for p in (tmcs_file, jst_file, roi_config_file) if not p.exists()]
    if missing:
        return failure_result(
            errors=[f"ROI_NOT_FOUND：查询理想ROI所需文件不存在：{', '.join(missing)}。"
                    "可用 --roi 手动指定目标投产比。"]
        )

    try:
        roi_config = load_roi_config(roi_config_file)
    except ValueError as exc:
        return failure_result(errors=[f"ROI_NOT_FOUND：加载ROI配置失败：{exc}"])

    try:
        # 商品ID = 商品编码，用 product_code 参数查询
        tmcs_result = find_tmcs_barcode(tmcs_file, product_code=flags.item_id)
    except ValueError as exc:
        return failure_result(
            errors=[f"ROI_NOT_FOUND：猫超商品列表中未找到商品ID {flags.item_id!r}：{exc}。"
                    "可用 --roi 手动指定目标投产比。"]
        )

    try:
        # 全站推 ROI：淘系控价单格多值时取最高价测算（更稳健，不因多值中止）。
        jst_result = find_jst_product(
            jst_file, tmcs_result["barcode"], multi_price_strategy="max"
        )
    except ValueError as exc:
        return failure_result(
            errors=[f"ROI_NOT_FOUND：聚水潭商品资料查询失败：{exc}。"
                    "可用 --roi 手动指定目标投产比。"]
        )

    try:
        roi_result = _calculate_roi(jst_result["price"], jst_result["cost"], config=roi_config)
    except ValueError as exc:
        return failure_result(errors=[f"ROI_NOT_FOUND：ROI计算失败：{exc}。"])

    ideal_roi = roi_result.get("ideal_roi")
    if ideal_roi is None:
        return failure_result(
            errors=["ROI_NOT_FOUND：理想ROI无法计算，"
                    "请用 --roi 手动指定目标投产比后再创建计划。"]
        )

    target_roi = round(ideal_roi, 2)
    ctx.state["target_roi"] = target_roi
    ctx.state["roi_source"] = "tmcs_sku_roi"
    ctx.state["roi_details"] = roi_result.get("details", {})
    return success_result(
        outputs={
            "target_roi": target_roi,
            "roi_source": "tmcs_sku_roi",
            "barcode": tmcs_result["barcode"],
            "price": jst_result["price"],
            "cost": jst_result["cost"],
        }
    )


def preview_plan_payload(ctx: StepContext):
    flags = ctx.state["flags"]
    payload: dict[str, Any] = {
        "item_id": flags.item_id,
        "plan_name": ctx.state["plan_name"],
        "daily_budget": flags.daily_budget,
        "target_roi": ctx.state["target_roi"],
        "roi_source": ctx.state.get("roi_source", "unknown"),
        "execute": flags.execute and not flags.dry_run,
        "dry_run": flags.dry_run,
    }
    ctx.state["preview_payload"] = payload
    return success_result(outputs={"preview": payload})


def create_zdx_plan(ctx: StepContext):
    flags = ctx.state["flags"]
    plan_name: str = ctx.state["plan_name"]
    target_roi: float = ctx.state["target_roi"]

    command = [
        "tmcs", "zdx", "fullsite-plan", "create",
        "--item-id", str(flags.item_id),
        "--plan-name", plan_name,
        "--daily-budget", str(flags.daily_budget),
        "--target-roi", str(target_roi),
    ]

    if flags.dry_run or not flags.execute:
        command.append("--dry-run")
    else:
        command.append("--execute")

    try:
        payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        return failure_result(errors=[f"Ops-Cli 调用失败：{exc}"])

    data: dict[str, Any] = payload.get("data") or {}
    ctx.state["ops_data"] = data
    ctx.state["created"] = bool(data.get("created", False))
    ctx.state["platform_plan_id"] = data.get("platform_plan_id")
    return success_result(
        outputs={
            "created": ctx.state["created"],
            "executed": bool(data.get("executed", False)),
            "simulated": bool(data.get("simulated", False)),
            "platform_plan_id": ctx.state["platform_plan_id"],
            "ops_context_path": data.get("context_path"),
        }
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    plan_name: str = ctx.state["plan_name"]
    target_roi: float = ctx.state["target_roi"]
    ops_data: dict[str, Any] = ctx.state.get("ops_data", {})

    outputs = {
        "item_id": flags.item_id,
        "plan_name": plan_name,
        "daily_budget": flags.daily_budget,
        "target_roi": target_roi,
        "roi_source": ctx.state.get("roi_source", "unknown"),
        "created": ctx.state.get("created", False),
        "platform_plan_id": ctx.state.get("platform_plan_id"),
        "dry_run": flags.dry_run,
        "execute": flags.execute and not flags.dry_run,
        "simulated": bool(ops_data.get("simulated", True)),
    }

    artifacts: list[Artifact] = []
    return success_result(outputs=outputs, artifacts=artifacts)
