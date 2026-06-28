from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from core.config_loader import get_path
from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from clients.ops_cli_client import OpsCommandResult, run_ops_command
from workflows.jst_massage_chair_order_remark.excel_lookup import load_massage_chair_mapping as load_mapping_file


# 内置默认值：配置文件缺失/字段缺失时回退，保证零配置也能跑。
# 店铺/状态/关键词等业务参数优先从 config/jst_massage_chair_order_remark.json 读取。
DEFAULT_SHOP_NAME = "（猫超）福安市启明工贸有限公司（肖国清）"
# 状态多值用逗号分隔（平台层 _split_status_filter 客户端精确匹配订单行 status）。
# 「异常」状态订单也要纳入自动备注（聚水潭真实状态文本即「异常」，已实测）。
DEFAULT_STATUS = "已付款待审核,异常"
DEFAULT_KEYWORD = "按摩椅"
DEFAULT_SOURCE_FILE_KEY = "massage_chair_mapping_file"
CONFIG_FILE_NAME = "jst_massage_chair_order_remark.json"


def _load_remark_config() -> dict[str, str]:
    """从 config/jst_massage_chair_order_remark.json 读取店铺/状态/关键词业务配置。

    文件缺失、解析失败或字段缺失时回退到内置默认值，保证零配置也能跑。
    """
    defaults = {
        "shop_name": DEFAULT_SHOP_NAME,
        "status": DEFAULT_STATUS,
        "keyword": DEFAULT_KEYWORD,
    }
    config_file = get_path("project_root") / "config" / CONFIG_FILE_NAME
    try:
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    return {key: (str(raw[key]).strip() or default) if raw.get(key) else default for key, default in defaults.items()}


@dataclass
class RemarkWorkflowState:
    flags: argparse.Namespace | None = None
    source_file: Path | None = None
    orders: list[dict[str, Any]] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)
    remark_plan: list[dict[str, Any]] = field(default_factory=list)
    remark_results: list[dict[str, Any]] = field(default_factory=list)
    normalize_results: list[dict[str, Any]] = field(default_factory=list)


def _state(ctx: StepContext) -> RemarkWorkflowState:
    return ctx.typed_state(RemarkWorkflowState)


def _flags(ctx: StepContext) -> argparse.Namespace:
    flags = _state(ctx).flags
    if flags is None:
        raise RuntimeError("workflow state missing flags")
    return flags


def _source_file(ctx: StepContext) -> Path:
    source_file = _state(ctx).source_file
    if source_file is None:
        raise RuntimeError("workflow state missing source_file")
    return source_file


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    config = _load_remark_config()
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--date", default=None)
    parser.add_argument("--order-id", action="append", default=[])
    parser.add_argument("--shop-name", default=config["shop_name"])
    parser.add_argument("--status", default=config["status"])
    parser.add_argument("--keyword", default=config["keyword"])
    parser.add_argument("--source-file", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.source_file = str(Path(namespace.source_file or get_path(DEFAULT_SOURCE_FILE_KEY)).expanduser())
    namespace.dry_run = ctx.dry_run or namespace.dry_run or not namespace.execute
    return namespace


def _order_id(order: dict[str, Any]) -> str:
    return str(order.get("order_id") or order.get("outer_order_id") or "").strip()


def _default_query_dates() -> list[str]:
    today = datetime.now().astimezone().date()
    yesterday = today - timedelta(days=1)
    return [today.isoformat(), yesterday.isoformat()]


def _item_text(item: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            str(item.get("product_name") or "").strip(),
            str(item.get("product_code") or "").strip(),
        )
        if part
    )


def _candidate_items(order: dict[str, Any], keyword: str) -> tuple[list[dict[str, Any]], str | None]:
    items = [item for item in (order.get("items") or []) if isinstance(item, dict)]
    matched = [item for item in items if keyword and keyword in _item_text(item)]
    if matched:
        return matched, None
    if len(items) == 1:
        return items, None
    return [], "ambiguous_items"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _build_plan_item(order: dict[str, Any], mapping: dict[str, str], keyword: str) -> dict[str, Any]:
    base = {
        "order_id": str(order.get("order_id") or "").strip(),
        "outer_order_id": str(order.get("outer_order_id") or "").strip(),
        "shop_name": str(order.get("shop_name") or "").strip(),
        "status": str(order.get("status") or "").strip(),
        "pay_time": str(order.get("pay_time") or "").strip(),
        "remark": str(order.get("remark") or "").strip(),
    }
    if base["remark"]:
        return {**base, "action": "skip", "reason": "already_has_remark"}

    candidates, reason = _candidate_items(order, keyword)
    if reason:
        return {**base, "action": "skip", "reason": reason}
    product_codes = [str(item.get("product_code") or "").strip() for item in candidates]
    product_codes = [code for code in product_codes if code]
    if not product_codes:
        return {**base, "action": "skip", "reason": "missing_product_code"}

    missing = [code for code in product_codes if code not in mapping]
    if missing:
        return {**base, "action": "skip", "reason": "product_code_not_found", "product_codes": product_codes, "missing_product_codes": missing}

    remark_names = _unique([mapping[code] for code in product_codes])
    return {
        **base,
        "action": "remark",
        "reason": "",
        "product_codes": product_codes,
        "remark_text": "、".join(remark_names),
    }


def _write_plan_artifact(plan: list[dict[str, Any]], *, dry_run: bool) -> Artifact:
    output_dir = get_path("runtime_dir") / "artifacts" / "jst_massage_chair_order_remark"
    output_dir.mkdir(parents=True, exist_ok=True)
    mode = "dry_run" if dry_run else "execute"
    path = output_dir / f"remark_plan_{mode}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"remark_plan": plan}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return Artifact(type="json", role="remark_plan", name=path.name, path=str(path), platform="jst")


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    source_file = Path(flags.source_file).expanduser().resolve()
    if not source_file.exists():
        return failure_result(f"按摩椅资料表不存在：{source_file}")
    if flags.limit is not None and flags.limit <= 0:
        return failure_result("--limit 必须大于 0")

    state = _state(ctx)
    state.flags = flags
    state.source_file = source_file
    return success_result(
        outputs={
            "date": flags.date,
            "order_ids": list(flags.order_id),
            "shop_name": flags.shop_name,
            "status": flags.status,
            "keyword": flags.keyword,
            "source_file": str(source_file),
            "limit": flags.limit,
            "dry_run": flags.dry_run,
            "execute": flags.execute,
        }
    )


def fetch_orders(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    query_dates = [flags.date] if flags.date else _default_query_dates()
    orders: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()
    filters_summary: list[dict[str, Any]] = []

    for query_date in query_dates:
        cmd = [
            "jst",
            "order",
            "query",
            "--date",
            query_date,
            "--shop-name",
            flags.shop_name,
            "--status",
            flags.status,
            "--keyword",
            flags.keyword,
            "--output",
            "json",
        ]
        for order_id in flags.order_id:
            cmd += ["--order-id", order_id]
        if flags.limit is not None:
            cmd += ["--limit", str(flags.limit)]
        try:
            result = run_ops_command(cmd, interactive_recovery=not flags.dry_run)
        except RuntimeError as exc:
            if flags.dry_run:
                state.orders = []
                return success_result(outputs={"skipped": True, "reason": str(exc), "orders": []})
            return failure_result(f"查询聚水潭订单失败：{exc}")

        data = result.data
        batch_orders = data.get("orders")
        batch_orders = [order for order in batch_orders if isinstance(order, dict)]
        for order in batch_orders:
            order_id = _order_id(order)
            dedupe_key = order_id or json.dumps(order, ensure_ascii=False, sort_keys=True)
            if dedupe_key in seen_order_ids:
                continue
            seen_order_ids.add(dedupe_key)
            orders.append(order)
        if isinstance(data, dict):
            filters_summary.append(data.get("filters") or {"date": query_date})
        if flags.limit is not None and len(orders) >= flags.limit:
            orders = orders[: flags.limit]
            break

    state.orders = orders
    return success_result(outputs={"total_orders": len(orders), "filters": filters_summary, "query_dates": query_dates})


def load_massage_chair_mapping(ctx: StepContext):
    state = _state(ctx)
    source_file = _source_file(ctx)
    try:
        mapping = load_mapping_file(source_file)
    except Exception as exc:
        return failure_result(f"读取按摩椅资料表失败：{exc}")
    state.mapping = mapping
    return success_result(outputs={"source_file": str(source_file), "mapping_count": len(mapping)})


def build_remark_plan(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    orders = state.orders
    mapping = state.mapping
    plan = [_build_plan_item(order, mapping, flags.keyword) for order in orders]
    state.remark_plan = plan
    to_remark = [item for item in plan if item.get("action") == "remark"]
    skipped = [item for item in plan if item.get("action") == "skip"]
    artifact = _write_plan_artifact(plan, dry_run=flags.dry_run)
    return success_result(
        outputs={
            "remark_plan": plan,
            "to_remark_count": len(to_remark),
            "skipped_count": len(skipped),
        },
        artifacts=[artifact],
    )


def apply_remarks(ctx: StepContext):
    state = _state(ctx)
    flags = _flags(ctx)
    plan = state.remark_plan
    executable = [item for item in plan if item.get("action") == "remark"]
    if flags.dry_run or not flags.execute:
        state.remark_results = []
        return success_result(outputs={"skipped": True, "reason": "dry-run 或未指定 --execute，不写聚水潭", "executed_count": 0, "failed_count": 0})

    results: list[dict[str, Any]] = []
    for item in executable:
        order_id = _order_id(item)
        if not order_id:
            results.append({**item, "success": False, "error": "missing_order_id"})
            continue
        cmd = ["jst", "order", "remark", "--order-id", order_id, "--remark-text", str(item["remark_text"]), "--execute"]
        try:
            result = run_ops_command(cmd, interactive_recovery=True)
            results.append({**item, "success": result.success, "payload": result.data})
        except RuntimeError as exc:
            results.append({**item, "success": False, "error": str(exc)})
    state.remark_results = results
    failed_count = sum(1 for item in results if not item.get("success"))
    outputs = {"executed_count": len(results) - failed_count, "failed_count": failed_count, "results": results}
    if failed_count:
        errors = [
            f"订单备注失败：{item.get('order_id') or item.get('outer_order_id') or 'unknown'}；{item.get('error') or 'unknown'}"
            for item in results
            if not item.get("success")
        ]
        return failure_result(errors, outputs=outputs)
    return success_result(outputs=outputs)


def _is_abnormal(item: dict[str, Any]) -> bool:
    return str(item.get("status") or "").strip() == "异常"


def normalize_abnormal_orders(ctx: StepContext):
    """对「异常状态 + 成功备注」的订单执行转正常单（jst order normalize / UnQuestions）。

    只处理本次成功写过备注的异常单；本身不是异常的不动。dry-run / 未 --execute 不写。
    """
    state = _state(ctx)
    flags = _flags(ctx)
    targets = [item for item in state.remark_results if item.get("success") and _is_abnormal(item)]

    if flags.dry_run or not flags.execute:
        return success_result(
            outputs={"skipped": True, "reason": "dry-run 或未指定 --execute，不转正常单", "to_normalize_count": len(targets)}
        )
    if not targets:
        return success_result(outputs={"normalized_count": 0, "failed_count": 0, "reason": "无成功备注的异常单"})

    results: list[dict[str, Any]] = []
    for item in targets:
        order_id = _order_id(item)
        if not order_id:
            results.append({**item, "normalized": False, "error": "missing_order_id"})
            continue
        cmd = ["jst", "order", "normalize", "--order-id", order_id, "--execute"]
        try:
            result = run_ops_command(cmd, interactive_recovery=True)
            summary = (result.data or {}).get("summary") if isinstance(result.data, dict) else None
            ok = bool(summary.get("success", 0) >= 1) if isinstance(summary, dict) else bool(result.success)
            results.append({**item, "normalized": ok, "payload": result.data})
        except RuntimeError as exc:
            results.append({**item, "normalized": False, "error": str(exc)})
    state.normalize_results = results
    failed = sum(1 for r in results if not r.get("normalized"))
    outputs = {"normalized_count": len(results) - failed, "failed_count": failed, "results": results}
    if failed:
        errors = [
            f"异常转正常失败：{r.get('order_id') or 'unknown'}；{r.get('error') or 'unknown'}"
            for r in results
            if not r.get("normalized")
        ]
        return failure_result(errors, outputs=outputs)
    return success_result(outputs=outputs)


def collect_outputs(ctx: StepContext):
    state = _state(ctx)
    plan = state.remark_plan
    results = state.remark_results
    flags = _flags(ctx)
    to_remark_count = sum(1 for item in plan if item.get("action") == "remark")
    skipped_count = sum(1 for item in plan if item.get("action") == "skip")
    failed_count = sum(1 for item in results if not item.get("success"))
    normalize_results = state.normalize_results
    abnormal_remarked = sum(1 for item in results if item.get("success") and _is_abnormal(item))
    normalized_count = sum(1 for item in normalize_results if item.get("normalized"))
    normalize_failed = sum(1 for item in normalize_results if not item.get("normalized"))
    return success_result(
        outputs={
            "task": "jst_massage_chair_order_remark",
            "dry_run": flags.dry_run,
            "execute": flags.execute,
            "total_orders": len(state.orders),
            "to_remark_count": to_remark_count,
            "skipped_count": skipped_count,
            "executed_count": len(results) - failed_count,
            "failed_count": failed_count,
            "abnormal_remarked_count": abnormal_remarked,
            "normalized_count": normalized_count,
            "normalize_failed_count": normalize_failed,
            "remark_plan": plan,
        }
    )
