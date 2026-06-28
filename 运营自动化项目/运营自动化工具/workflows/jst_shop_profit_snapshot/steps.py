"""聚水潭店铺利润快照 workflow step handler。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config_loader import get_path
from core.runtime import Artifact, StepContext, failure_result, parse_workflow_args, success_result

from clients.ops_cli_client import run_ops_json


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--month", default=None)
    parser.add_argument("--date", default=None, help="任意单日 YYYY-MM-DD / today / yesterday；与 --month 互斥")
    parser.add_argument("--shop", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--metrics", default=None, help="按名称(逗号分隔)从全部利润科目与 KPI 里挑选，如 营销费用,财务费用,毛利率,客单价")
    parser.add_argument("--full", action="store_true", help="outputs 额外带上完整 37 条利润科目 metrics（默认只给摘要）")
    parser.add_argument("--dry-run", action="store_true")
    flags = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    flags.dry_run = ctx.dry_run or flags.dry_run
    return flags


# 经营利润多维度报表的关键科目：name 形如「编码：标签」(如 660101：营销费用) 或纯名称
# (销售收入/毛利额/经营利润)。code 用编码精确匹配避免父子串扰，name/contains 容错匹配。
# 匹配不到的科目直接跳过，绝不报错——保证不影响原始 metrics/快照。
_SUMMARY_SPEC: list[tuple[str, str, str]] = [
    ("销售收入", "name", "销售收入"),
    ("付款金额", "contains", "付款金额"),
    ("销售成本", "name", "销售成本"),
    ("毛利额", "name", "毛利额"),
    ("销售费用", "code", "6601"),
    ("营销费用", "code", "660101"),
    ("平台费用", "code", "660102"),
    ("店铺直接运营费用", "code", "660103"),
    ("售后费用", "code", "660104"),
    ("人工干预费用", "code", "660105"),
    ("管理费用", "code", "6603"),
    ("财务费用", "code", "6604"),
    ("快递费用", "code", "6605"),
    ("经营利润", "name", "经营利润"),
]


def _metric_code(name: str) -> str:
    for sep in ("：", ":"):
        if sep in name:
            head = name.split(sep, 1)[0].strip()
            return head if head.isdigit() else ""
    return ""


def _match_metric(metric: dict[str, Any], kind: str, key: str) -> bool:
    name = str(metric.get("name") or "").strip()
    if kind == "code":
        return _metric_code(name) == key
    if kind == "name":
        return name == key
    if kind == "contains":
        return key in name
    return False


def build_financial_summary(metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for label, kind, key in _SUMMARY_SPEC:
        match = next((m for m in metrics if _match_metric(m, kind, key)), None)
        if match is None:
            continue
        summary.append(
            {
                "label": label,
                "value": match.get("value"),
                "percent": match.get("percent"),
                "source_name": str(match.get("name") or ""),
            }
        )
    return summary


# 经营利润多维度报表 summaryData 里的运营 KPI（不含「退货后/ByReturn」系列）。
# 取不到的 key 直接跳过，绝不报错——保证不影响原始数据/快照。
_KPI_SPEC: list[tuple[str, str]] = [
    ("毛利率", "grossProfitRate"),
    ("退款率(发货前)", "refundratePre"),
    ("退款率(发货后)", "refundrateAfter"),
    ("单量", "billQuantity"),
    ("客单价", "avgBillSalePrice"),
    ("单均件数", "avgBillQuantity"),
    ("商品件数", "goodsQuantity"),
    ("倍率", "priceMultiple"),
    ("件均成本", "avgSkuCostAmount"),
]


def build_kpi_summary(summary_data: dict[str, Any]) -> list[dict[str, Any]]:
    kpis: list[dict[str, Any]] = []
    for label, key in _KPI_SPEC:
        if key not in summary_data:
            continue
        kpis.append({"label": label, "key": key, "value": summary_data.get(key)})
    return kpis


def select_metrics(
    metrics: list[dict[str, Any]],
    terms: list[str],
    *,
    kpis: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in terms:
        needle = term.strip()
        if not needle:
            continue
        for m in metrics:
            name = str(m.get("name") or "")
            if needle in name and ("metric:" + name) not in seen:
                seen.add("metric:" + name)
                selected.append({"matched": needle, "name": name, "value": m.get("value"), "percent": m.get("percent")})
        for k in kpis or []:
            label = str(k.get("label") or "")
            if needle in label and ("kpi:" + label) not in seen:
                seen.add("kpi:" + label)
                selected.append({"matched": needle, "name": label, "value": k.get("value"), "kind": "kpi"})
    return selected


def _default_output_path(*, month: str | None, date_value: str | None) -> Path:
    stamp = month or date_value or datetime.now().strftime("%Y-%m-%d")
    safe_stamp = stamp.replace("/", "-")
    return get_path("runtime_dir") / "artifacts" / "jst_shop_profit_snapshot" / f"profit_{safe_stamp}.json"


def _snapshot_payload(data: dict[str, Any], *, period: str, month: str | None = None) -> dict[str, Any]:
    payload = {
        "period": period,
        "store": data.get("store"),
        "profit": data.get("profit"),
        "metric_field": data.get("metric_field"),
        "scene": data.get("scene"),
        "source": data.get("source"),
        "metrics": data.get("metrics") or [],
        "financial_summary": build_financial_summary(data.get("metrics") or []),
        "kpi_summary": build_kpi_summary(((data.get("raw_data") or {}).get("summaryData") or {})),
        "summary_data_keys": data.get("summary_data_keys") or [],
        "raw_data": data.get("raw_data") or {},
        "raw_response": data.get("raw_response") or {},
    }
    if month:
        payload["month"] = month
    if data.get("date"):
        payload["date"] = data.get("date")
    if data.get("context_path"):
        payload["ops_context_path"] = data.get("context_path")
    return payload


def _resolve_period(flags: argparse.Namespace) -> str:
    if flags.month and flags.date:
        raise ValueError("--month 与 --date 互斥，只能二选一")
    if flags.month:
        return "month"
    if flags.date:
        return "day"
    return "yesterday"


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags
    try:
        period = _resolve_period(flags)
    except ValueError as exc:
        return failure_result(errors=[str(exc)])
    return success_result(
        outputs={
            "period": period,
            "month": flags.month,
            "date": flags.date,
            "shop": flags.shop,
            "dry_run": flags.dry_run,
            "output": flags.output,
        }
    )


def fetch_profit_detail(ctx: StepContext):
    flags = ctx.state["flags"]
    period = _resolve_period(flags)
    if period == "month":
        command = ["--json", "jst", "profit", "month", "--month", flags.month, "--detail"]
    elif period == "day":
        command = ["--json", "jst", "profit", "day", "--date", flags.date, "--detail"]
    else:
        command = ["--json", "jst", "profit", "yesterday", "--detail"]
    if flags.shop:
        command.extend(["--shop", flags.shop])

    ctx.state["ops_command"] = command
    ctx.state["period"] = period
    if flags.dry_run:
        ctx.state["profit_data"] = {}
        return success_result(outputs={"planned": True, "ops_command": command, "metric_count": 0})

    try:
        payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        return failure_result(errors=[f"Ops-Cli 调用失败：{exc}"], outputs={"ops_command": command})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    ctx.state["profit_data"] = data
    return success_result(
        outputs={
            "profit": data.get("profit"),
            "metric_field": data.get("metric_field"),
            "metric_count": len(data.get("metrics") or []),
            "ops_context_path": data.get("context_path"),
        }
    )


def write_snapshot(ctx: StepContext):
    flags = ctx.state["flags"]
    data = ctx.state["profit_data"]
    period = ctx.state["period"]
    output_path = Path(flags.output).expanduser() if flags.output else _default_output_path(
        month=data.get("month"),
        date_value=data.get("date"),
    )
    if flags.dry_run:
        ctx.state["output_path"] = str(output_path)
        return success_result(outputs={"planned": True, "written": False, "planned_output_path": str(output_path)})
    snapshot = _snapshot_payload(data, period=period, month=data.get("month"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ctx.state["snapshot"] = snapshot
    ctx.state["output_path"] = str(output_path)
    artifact = Artifact(type="json", role="profit_snapshot", name=output_path.name, path=str(output_path), platform="jst", month=str(data.get("month") or ""))
    return success_result(
        outputs={"output_path": str(output_path), "written": True, "metric_count": len(snapshot.get("metrics") or [])},
        artifacts=[artifact],
    )


def collect_outputs(ctx: StepContext):
    snapshot = ctx.state.get("snapshot") or {}
    period = snapshot.get("period") or ctx.state.get("period")
    flags = ctx.state.get("flags")
    # 摘要直接从已抓到的 metrics / summaryData 算（dry-run 无数据时为空，不报错）。
    profit_data = ctx.state.get("profit_data") or {}
    metrics = profit_data.get("metrics") or []
    summary_data = (profit_data.get("raw_data") or {}).get("summaryData") or {}
    financial_summary = build_financial_summary(metrics)
    kpi_summary = build_kpi_summary(summary_data)
    outputs = {
        "task": "jst_shop_profit_snapshot",
        "period": period,
        "date": snapshot.get("date"),
        "month": snapshot.get("month"),
        "store": snapshot.get("store"),
        "profit": snapshot.get("profit"),
        "metric_field": snapshot.get("metric_field"),
        "metric_count": len(metrics),
        "financial_summary": financial_summary,
        "kpi_summary": kpi_summary,
        "output_path": ctx.state.get("output_path"),
    }
    # --full：额外带上完整 37 条利润科目（默认只给摘要，避免撑大 outputs/上下文）。
    if getattr(flags, "full", False):
        outputs["metrics"] = metrics
    # --metrics：从全部利润科目与 KPI 里按名称挑选。
    requested = getattr(flags, "metrics", None) if flags is not None else None
    if requested:
        outputs["selected_metrics"] = select_metrics(metrics, requested.split(","), kpis=kpi_summary)
    return success_result(outputs=outputs)
