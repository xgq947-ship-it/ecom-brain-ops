from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from core.config_loader import get_path
from core.runtime import parse_workflow_args, Artifact, StepContext, WorkflowRunner, failure_result, success_result
from core.runtime.registry import discover_workflow

from workflows.tmcs_priority_promotion_plan_create.excel_filter import (
    PRIORITY_ITEM_HEADERS,
    PRIORITY_PRODUCT_HEADERS,
    append_active_style_codes,
    load_active_style_codes,
    load_priority_promotion_rows,
    load_tmcs_master_mapping,
    normalize_cell,
    write_result_csv,
    write_result_excel,
    write_result_json,
)


_DEFAULT_ACTIVE_PROMOTION_FILE = get_path("project_root").parent / "主数据" / "正在推广商品列表.xlsx"
_DEFAULT_TMCS_MASTER_FILE = get_path("tmall_goods_master_file")


@dataclass
class PromotionWorkflowState:
    flags: argparse.Namespace | None = None
    priority_items: list[dict[str, Any]] = field(default_factory=list)
    generated_source_run_id: str | None = None
    active_codes: list[str] = field(default_factory=list)
    skipped_items: list[dict[str, Any]] = field(default_factory=list)
    to_create_candidates: list[dict[str, Any]] = field(default_factory=list)
    to_create_items: list[dict[str, Any]] = field(default_factory=list)
    plan_payloads: list[dict[str, Any]] = field(default_factory=list)
    created_items: list[dict[str, Any]] = field(default_factory=list)
    failed_items: list[dict[str, Any]] = field(default_factory=list)
    active_promotion_appended_codes: list[str] = field(default_factory=list)
    output_path: str | None = None


def _state(ctx: StepContext) -> PromotionWorkflowState:
    return ctx.typed_state(PromotionWorkflowState)


def _flags(ctx: StepContext) -> argparse.Namespace:
    flags = _state(ctx).flags
    if flags is None:
        raise RuntimeError("workflow state missing flags")
    return flags


def _last_month() -> str:
    first_of_this_month = date.today().replace(day=1)
    return (first_of_this_month - timedelta(days=1)).strftime("%Y-%m")


def _default_promotion_list_file(month: str) -> Path:
    return get_path("project_root") / "output" / f"猫超推广清单_{month}.xlsx"


def _default_plan_name(item_id: str) -> str:
    return f"全站推广_{item_id}_{date.today().strftime('%m%d')}"


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--month", default=None)
    parser.add_argument("--promotion-list-file", default=None)
    parser.add_argument("--active-promotion-file", default=str(_DEFAULT_ACTIVE_PROMOTION_FILE))
    parser.add_argument("--tmcs-master-file", default=str(_DEFAULT_TMCS_MASTER_FILE))
    parser.add_argument("--daily-budget", type=float, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--auto-generate-source", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    if not namespace.month:
        namespace.month = _last_month()
    namespace.dry_run = bool(ctx.dry_run or namespace.dry_run or not namespace.execute)
    if namespace.promotion_list_file:
        namespace.promotion_list_file = str(Path(namespace.promotion_list_file).expanduser())
    else:
        namespace.promotion_list_file = str(_default_promotion_list_file(namespace.month))
    namespace.active_promotion_file = str(Path(namespace.active_promotion_file).expanduser())
    namespace.tmcs_master_file = str(Path(namespace.tmcs_master_file).expanduser())
    return namespace


def _validate_month(month: str) -> bool:
    try:
        year_text, month_text = month.split("-", 1)
        year = int(year_text)
        month_value = int(month_text)
        return len(year_text) == 4 and 1 <= month_value <= 12 and year >= 2000
    except ValueError:
        return False


def _run_child_workflow(workflow_id: str, args: list[str], *, dry_run: bool):
    workflow = discover_workflow(workflow_id)
    inputs = {"args": list(args)}
    if dry_run:
        inputs["dry_run"] = True
    return WorkflowRunner(get_path("runtime_dir") / "runs").run(workflow, inputs=inputs, dry_run=dry_run)


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    if not _validate_month(flags.month):
        return failure_result(errors=[f"--month 格式必须是 YYYY-MM，当前值：{flags.month}"])
    if flags.daily_budget is None:
        return failure_result(errors=["--daily-budget 必填；当前项目没有默认预算配置。"])
    if flags.daily_budget <= 0:
        return failure_result(errors=[f"--daily-budget 必须大于 0，当前值：{flags.daily_budget}"])
    if flags.limit is not None and flags.limit <= 0:
        return failure_result(errors=[f"--limit 必须大于 0，当前值：{flags.limit}"])

    promotion_list_file = Path(flags.promotion_list_file)
    active_promotion_file = Path(flags.active_promotion_file)
    tmcs_master_file = Path(flags.tmcs_master_file)
    if not flags.auto_generate_source and not promotion_list_file.is_file():
        return failure_result(errors=[f"推广清单不存在：{promotion_list_file}"])
    if not active_promotion_file.is_file():
        return failure_result(errors=[f"正在推广商品列表不存在：{active_promotion_file}"])
    if not tmcs_master_file.is_file():
        return failure_result(errors=[f"猫超商品主表不存在：{tmcs_master_file}"])

    _state(ctx).flags = flags
    return success_result(
        outputs={
            "month": flags.month,
            "promotion_list_file": str(promotion_list_file),
            "active_promotion_file": str(active_promotion_file),
            "tmcs_master_file": str(tmcs_master_file),
            "daily_budget": flags.daily_budget,
            "limit": flags.limit,
            "dry_run": flags.dry_run,
            "execute": flags.execute and not flags.dry_run,
            "auto_generate_source": flags.auto_generate_source,
            "output": flags.output,
        }
    )


def load_priority_promotion_list(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    promotion_list_file = Path(flags.promotion_list_file)
    generated_run_id = None

    if flags.auto_generate_source:
        child_args = ["--month", flags.month, "--output", str(promotion_list_file)]
        if flags.dry_run:
            child_args.append("--dry-run")
        else:
            child_args.append("--execute")
        child_run = _run_child_workflow("jst_tmcs_shop_product_sales_analysis", child_args, dry_run=flags.dry_run)
        generated_run_id = child_run.run_id
        if child_run.status == "failed":
            return failure_result(errors=[f"自动生成推广清单失败：{'；'.join(child_run.errors) or child_run.status}"])

    if not promotion_list_file.is_file():
        return failure_result(errors=[f"推广清单不存在：{promotion_list_file}"])

    try:
        priority_items = load_priority_promotion_rows(promotion_list_file)
    except ValueError as exc:
        return failure_result(errors=[str(exc)])

    state.priority_items = priority_items
    state.generated_source_run_id = generated_run_id
    return success_result(
        outputs={
            "priority_items": priority_items,
            "priority_style_codes": [item["store_style_code"] for item in priority_items],
            "total_priority_count": len(priority_items),
            "generated_source_run_id": generated_run_id,
        },
        artifacts=[
            Artifact(
                type="xlsx",
                role="promotion_source",
                name=promotion_list_file.name,
                path=str(promotion_list_file),
                platform="tmcs",
                month=flags.month,
            )
        ],
    )


def load_active_promotion_list(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    active_promotion_file = Path(flags.active_promotion_file)
    try:
        active_codes = load_active_style_codes(active_promotion_file)
    except ValueError as exc:
        return failure_result(errors=[str(exc)])

    state.active_codes = active_codes
    return success_result(
        outputs={
            "active_style_codes": active_codes,
            "active_count": len(active_codes),
        },
        artifacts=[
            Artifact(
                type="xlsx",
                role="active_promotion_source",
                name=active_promotion_file.name,
                path=str(active_promotion_file),
                platform="tmcs",
                month=flags.month,
            )
        ],
    )


def filter_not_active(ctx: StepContext):
    state = _state(ctx)
    priority_items = state.priority_items
    active_codes = set(state.active_codes)
    skipped_items: list[dict[str, Any]] = []
    to_create_candidates: list[dict[str, Any]] = []

    for item in priority_items:
        store_style_code = item["store_style_code"]
        base = {
            "store_style_code": store_style_code,
            "product_code": normalize_cell(item.get("商品编码") or item.get("产品编码") or item.get("货品编码") or item.get("SKU编码")),
            "item_id": normalize_cell(item.get("商品ID") or item.get("平台商品ID") or item.get("item_id") or item.get("Item ID")),
        }
        if store_style_code in active_codes:
            skipped_items.append({**base, "skip_reason": "already_active", "reason": "already_active", "status": "skipped"})
            continue
        to_create_candidates.append(base)

    state.skipped_items = skipped_items
    state.to_create_candidates = to_create_candidates
    return success_result(
        outputs={
            "skipped_items": skipped_items,
            "to_create_candidates": to_create_candidates,
            "already_active_count": len(skipped_items),
        }
    )


def resolve_item_ids_for_plan(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    skipped_items = list(state.skipped_items)
    candidates = state.to_create_candidates
    try:
        master_mapping = load_tmcs_master_mapping(Path(flags.tmcs_master_file))
    except ValueError as exc:
        return failure_result(errors=[str(exc)])

    resolved: list[dict[str, Any]] = []
    missing_count = 0
    for item in candidates:
        store_style_code = item["store_style_code"]
        product_code = normalize_cell(item.get("product_code"))
        item_id = normalize_cell(item.get("item_id"))
        if not item_id or not product_code:
            mapped = master_mapping.get(store_style_code)
            if mapped:
                product_code = product_code or mapped.get("product_code", "")
                item_id = item_id or mapped.get("item_id", "")
        if not product_code and item_id:
            product_code = item_id
        if not item_id and product_code:
            item_id = product_code
        if not item_id:
            skipped_items.append(
                {
                    "store_style_code": store_style_code,
                    "product_code": product_code,
                    "item_id": "",
                    "skip_reason": "missing_item_id",
                    "reason": "missing_item_id",
                    "status": "skipped",
                }
            )
            missing_count += 1
            continue
        resolved.append(
            {
                "store_style_code": store_style_code,
                "product_code": product_code,
                "item_id": item_id,
                "status": "ready",
            }
        )

    state.skipped_items = skipped_items
    state.to_create_items = resolved
    return success_result(
        outputs={
            "to_create_items": resolved,
            "skipped_items": skipped_items,
            "missing_item_id_count": missing_count,
            "to_create_count": len(resolved),
        }
    )


def build_create_plan_payloads(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    limit = flags.limit or None
    source_items = state.to_create_items
    payloads: list[dict[str, Any]] = []
    for item in source_items[:limit]:
        payloads.append(
            {
                "store_style_code": item["store_style_code"],
                "product_code": item["product_code"],
                "item_id": item["item_id"],
                "daily_budget": flags.daily_budget,
                "target_workflow": "tmcs_zdx_fullsite_plan_create",
                "dry_run": flags.dry_run,
                "execute": flags.execute and not flags.dry_run,
                "plan_name": _default_plan_name(item["item_id"]),
            }
        )
    state.plan_payloads = payloads
    return success_result(outputs={"plan_payloads": payloads, "plan_payload_count": len(payloads)})


def create_zdx_plans(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    payloads = state.plan_payloads
    created_items: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []

    if flags.dry_run:
        state.created_items = created_items
        state.failed_items = failed_items
        return success_result(
            outputs={
                "skipped": True,
                "reason": "dry-run：仅输出将要调用 tmcs_zdx_fullsite_plan_create 的 payload",
                "payloads": payloads,
                "created_count": 0,
                "failed_count": 0,
            }
        )

    for payload in payloads:
        child_args = [
            "--item-id",
            payload["item_id"],
            "--daily-budget",
            str(payload["daily_budget"]),
            "--plan-name",
            payload["plan_name"],
            "--confirm-plan-name",
            payload["plan_name"],
            "--execute",
        ]
        child_run = _run_child_workflow("tmcs_zdx_fullsite_plan_create", child_args, dry_run=False)
        if child_run.status == "failed":
            failed_items.append(
                {
                    **payload,
                    "status": "failed",
                    "error": "；".join(child_run.errors) or child_run.status,
                    "child_run_id": child_run.run_id,
                }
            )
            continue
        child_outputs = child_run.outputs or {}
        created_items.append(
            {
                **payload,
                "status": "created" if child_outputs.get("created") else "executed",
                "platform_plan_id": child_outputs.get("platform_plan_id"),
                "child_run_id": child_run.run_id,
            }
        )

    state.created_items = created_items
    state.failed_items = failed_items
    return success_result(
        outputs={
            "created_items": created_items,
            "failed_items": failed_items,
            "created_count": len(created_items),
            "failed_count": len(failed_items),
        }
    )


def sync_active_promotion_list(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    created_items = state.created_items
    if flags.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run：跳过回写正在推广商品列表"})
    if not created_items:
        return success_result(outputs={"written": False, "reason": "没有成功创建的计划，无需回写"})

    active_promotion_file = Path(flags.active_promotion_file)
    try:
        sync_result = append_active_style_codes(
            active_promotion_file,
            [item.get("store_style_code", "") for item in created_items],
        )
    except ValueError as exc:
        return failure_result(errors=[str(exc)])

    state.active_promotion_appended_codes = sync_result["appended_codes"]
    return success_result(
        outputs={
            "written": True,
            "active_promotion_file": str(active_promotion_file),
            "appended_codes": sync_result["appended_codes"],
            "appended_count": sync_result["appended_count"],
        }
    )


def write_outputs(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    payload = {
        "priority_items": state.priority_items,
        "skipped_items": state.skipped_items,
        "to_create_items": state.to_create_items,
        "created_items": state.created_items,
        "failed_items": state.failed_items,
    }
    if not flags.output:
        return success_result(outputs={"written": False, "reason": "未指定 --output，仅返回结果"})
    output_path = Path(str(flags.output)).expanduser()
    if flags.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run：跳过写出结果文件", "planned_output": str(output_path)})
    suffix = output_path.suffix.lower()
    if suffix == ".json":
        write_result_json(output_path, payload)
    elif suffix == ".xlsx":
        write_result_excel(output_path, payload)
    else:
        write_result_csv(output_path, payload)
    state.output_path = str(output_path)
    return success_result(
        outputs={"written": True, "output_path": str(output_path)},
        artifacts=[
            Artifact(
                type=output_path.suffix.lstrip(".") or "csv",
                role="output",
                name=output_path.name,
                path=str(output_path),
                platform="tmcs",
                month=flags.month,
            )
        ],
    )


def collect_outputs(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    priority_items = state.priority_items
    skipped_items = state.skipped_items
    to_create_items = state.to_create_items
    created_items = state.created_items
    failed_items = state.failed_items
    missing_item_id_count = sum(1 for item in skipped_items if item.get("skip_reason") == "missing_item_id")

    outputs = {
        "month": flags.month,
        "priority_items": priority_items,
        "skipped_items": skipped_items,
        "to_create_items": to_create_items,
        "created_items": created_items,
        "failed_items": failed_items,
        "promotion_list_file": flags.promotion_list_file,
        "active_promotion_file": flags.active_promotion_file,
        "output_path": state.output_path,
        "total_priority_count": len(priority_items),
        "already_active_count": sum(1 for item in skipped_items if item.get("skip_reason") == "already_active"),
        "missing_item_id_count": missing_item_id_count,
        "to_create_count": len(to_create_items),
        "created_count": len(created_items),
        "failed_count": len(failed_items),
        "active_promotion_appended_codes": state.active_promotion_appended_codes,
        "plan_payloads": state.plan_payloads,
        "generated_source_run_id": state.generated_source_run_id,
    }
    if failed_items:
        errors = [
            f"推广计划创建失败：{item.get('store_style_code') or item.get('item_id') or 'unknown'}；{item.get('error') or 'unknown'}"
            for item in failed_items
        ]
        return failure_result(errors=errors, outputs=outputs)
    return success_result(outputs=outputs)
