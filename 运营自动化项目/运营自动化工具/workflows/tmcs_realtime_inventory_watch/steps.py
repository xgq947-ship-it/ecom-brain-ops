"""猫超库存实时监测 workflow 的 step handler。

属"平台读取 + workflow 业务判断"类型：
- 平台数据刷新（聚水潭资料 / 猫超库存明细）全部复用既有 Ops-Cli 能力，经
  clients/ops_cli_client.run_ops_json 调用，本层不写 URL/Cookie/Token/Selector/Playwright。
- 本层只负责：读取本地 Excel、字段识别、合并、剩余库存与风险计算、产物与通知预览。

dry-run / 默认安全语义：
- 默认不触发任何平台真实下载；只有显式 --execute 且非 dry-run 才会刷新平台数据。
- 聚水潭资料默认读已落地的主数据文件（本地只读，非平台下载）。
- 猫超库存明细无本地文件且未 --execute 时视为不可用：dry-run 跳过、真实运行报清晰错误。
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from clients.ops_cli_client import run_ops_json
from core.config_loader import get_path
from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, send_notification, success_result

from workflows.tmcs_realtime_inventory_watch import excel_loader, inventory_analyzer

DEFAULT_BRANDS = ("苏泊尔", "奥克斯")
DEFAULT_WAREHOUSE_CODE = "mc_aokesi_suolong"
DEFAULT_THRESHOLD = 20.0
DEFAULT_TMCS_THRESHOLD = 50.0


# ── 参数解析 ──────────────────────────────────────────────────────────────────
def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--brands", nargs="+", default=list(DEFAULT_BRANDS))
    parser.add_argument("--warehouse-code", default=DEFAULT_WAREHOUSE_CODE)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--tmcs-threshold", type=float, default=DEFAULT_TMCS_THRESHOLD)
    parser.add_argument("--maochao-goods-file", default=None)
    parser.add_argument("--use-local-jst-file", default=None)
    parser.add_argument("--use-local-tmcs-stock-file", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    if not namespace.maochao_goods_file:
        namespace.maochao_goods_file = str(get_path("tmall_goods_master_file"))
    # --brands 允许逗号分隔（"苏泊尔,奥克斯"）或空格分隔
    brands: list[str] = []
    for item in namespace.brands:
        brands.extend(part.strip() for part in str(item).split(",") if part.strip())
    namespace.brands = brands
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    errors: list[str] = []
    if not flags.brands:
        errors.append("brands 不能为空")
    if not flags.warehouse_code:
        errors.append("warehouse_code 不能为空")
    if flags.threshold is None or flags.threshold < 0:
        errors.append(f"threshold 非法：{flags.threshold}")
    if flags.tmcs_threshold is None or flags.tmcs_threshold < 0:
        errors.append(f"tmcs_threshold 非法：{flags.tmcs_threshold}")
    if not Path(str(flags.maochao_goods_file)).expanduser().exists():
        errors.append(f"猫超商品列表文件不存在：{flags.maochao_goods_file}")
    if flags.use_local_jst_file and not Path(flags.use_local_jst_file).expanduser().exists():
        errors.append(f"--use-local-jst-file 文件不存在：{flags.use_local_jst_file}")
    if flags.use_local_tmcs_stock_file and not Path(flags.use_local_tmcs_stock_file).expanduser().exists():
        errors.append(f"--use-local-tmcs-stock-file 文件不存在：{flags.use_local_tmcs_stock_file}")
    if errors:
        return failure_result(errors)
    ctx.state["flags"] = flags
    ctx.state["warnings"] = []
    return success_result(
        outputs={
            "brands": flags.brands,
            "warehouse_code": flags.warehouse_code,
            "threshold": flags.threshold,
            "tmcs_threshold": flags.tmcs_threshold,
            "maochao_goods_file": str(flags.maochao_goods_file),
            "use_local_jst_file": flags.use_local_jst_file,
            "use_local_tmcs_stock_file": flags.use_local_tmcs_stock_file,
            "dry_run": flags.dry_run,
            "execute": flags.execute,
        }
    )


# ── 数据刷新（复用既有平台能力）──────────────────────────────────────────────
def refresh_jst_product_data(ctx: StepContext):
    """聚水潭商品资料（实时优先）：
    - 显式 --use-local-jst-file：用指定本地文件（回放/测试）。
    - 真实运行（默认）：实时经 `jst product sync` 从平台下载最新资料并写主数据，再读取。
    - dry-run：绝不下载，仅用现有主数据预览（可能非最新）。
    """
    flags = ctx.state["flags"]
    if flags.use_local_jst_file:
        path = Path(flags.use_local_jst_file).expanduser()
        ctx.state["jst_file"] = path
        return success_result(outputs={"jst_product_file": str(path), "source": "local_file", "fresh": False})

    master = Path(str(get_path("jst_product_file"))).expanduser()
    if flags.dry_run:
        if not master.exists():
            ctx.state["jst_file"] = None
            return success_result(outputs={"skipped": True, "reason": f"dry-run 不下载且主数据不存在：{master}"})
        ctx.state["jst_file"] = master
        return success_result(
            outputs={"jst_product_file": str(master), "source": "master_preview", "fresh": False,
                     "note": "dry-run 用现有主数据预览，未实时下载"}
        )

    # 真实运行：实时从平台下载（覆盖主数据为最新），再读取。
    payload = run_ops_json(["jst", "product", "sync", "--keep-brands", *flags.brands])
    data = payload.get("data") if isinstance(payload, dict) else {}
    out = (data or {}).get("output_path") or (data or {}).get("source")
    target = Path(str(out)).expanduser() if out else master
    if not target.exists():
        return failure_result(f"聚水潭商品资料实时下载后未找到文件：{target}")
    ctx.state["jst_file"] = target
    return success_result(
        outputs={"jst_product_file": str(target), "source": "platform_download", "fresh": True,
                 "downloaded": bool((data or {}).get("downloaded"))}
    )


def refresh_tmcs_stock_data(ctx: StepContext):
    """猫超库存明细（实时优先）：
    - 显式 --use-local-tmcs-stock-file：用指定本地文件（回放/测试）。
    - 真实运行（默认）：实时经 `tmcs inventory export` 从平台导出最新库存明细，再读取。
    - dry-run：绝不下载，跳过（无文件时风险分析为空）。
    """
    flags = ctx.state["flags"]
    if flags.use_local_tmcs_stock_file:
        path = Path(flags.use_local_tmcs_stock_file).expanduser()
        ctx.state["tmcs_file"] = path
        return success_result(outputs={"tmcs_stock_file": str(path), "source": "local_file", "fresh": False})

    if flags.dry_run:
        ctx.state["tmcs_file"] = None
        return success_result(outputs={"skipped": True, "reason": "dry-run 不下载猫超库存明细"})

    # 真实运行：实时从平台导出最新库存明细。
    payload = run_ops_json(["tmcs", "inventory", "export", "--warehouse-code", flags.warehouse_code])
    data = payload.get("data") if isinstance(payload, dict) else {}
    output_path = (data or {}).get("output_path")
    if not output_path or not Path(str(output_path)).expanduser().exists():
        return failure_result(f"猫超库存明细实时导出未产出可用文件：{output_path}")
    ctx.state["tmcs_file"] = Path(str(output_path)).expanduser()
    return success_result(
        outputs={"tmcs_stock_file": str(output_path), "source": "platform_download", "fresh": True}
    )


# ── 读取与计算 ────────────────────────────────────────────────────────────────
def load_maochao_goods(ctx: StepContext):
    flags = ctx.state["flags"]
    headers, records = excel_loader.read_rows(flags.maochao_goods_file)
    rows, warnings = inventory_analyzer.load_maochao_goods(headers, records)
    ctx.state["goods_rows"] = rows
    ctx.state["warnings"].extend(warnings)
    return success_result(outputs={"active_tmcs_goods_rows": len(rows), "warnings": warnings})


def load_jst_products(ctx: StepContext):
    flags = ctx.state["flags"]
    jst_file = ctx.state.get("jst_file")
    if not jst_file:
        ctx.state["jst_rows"] = []
        return success_result(outputs={"skipped": True, "reason": "无聚水潭资料文件", "jst_rows": 0})
    headers, records = excel_loader.read_rows(jst_file)
    rows, warnings = inventory_analyzer.load_jst_products(headers, records, brands=flags.brands)
    ctx.state["jst_rows"] = rows
    ctx.state["warnings"].extend(warnings)
    return success_result(outputs={"jst_rows": len(rows), "warnings": warnings})


def load_tmcs_stock(ctx: StepContext):
    flags = ctx.state["flags"]
    tmcs_file = ctx.state.get("tmcs_file")
    if not tmcs_file:
        ctx.state["tmcs_rows"] = []
        return success_result(outputs={"skipped": True, "reason": "无猫超库存明细文件", "tmcs_stock_rows": 0})
    headers, records = excel_loader.read_rows(tmcs_file)
    rows, warnings = inventory_analyzer.load_tmcs_stock(headers, records, warehouse_code=flags.warehouse_code)
    ctx.state["tmcs_rows"] = rows
    ctx.state["warnings"].extend(warnings)
    return success_result(outputs={"tmcs_stock_rows": len(rows), "warnings": warnings})


def build_inventory_table(ctx: StepContext):
    table, warnings = inventory_analyzer.build_inventory_table(
        ctx.state.get("jst_rows") or [], ctx.state.get("goods_rows") or []
    )
    ctx.state["inventory_table"] = table
    ctx.state["warnings"].extend(warnings)
    return success_result(outputs={"matched_rows": len(table), "warnings": warnings})


def detect_inventory_risks(ctx: StepContext):
    flags = ctx.state["flags"]
    table = ctx.state.get("inventory_table") or []
    tmcs_rows = ctx.state.get("tmcs_rows") or []
    risks, low_count = inventory_analyzer.detect_inventory_risks(table, tmcs_rows, threshold=flags.threshold)
    # 子表：排除风险表后，聚水潭实际库存>=threshold 且 猫超可售<tmcs_threshold
    low_tmcs = inventory_analyzer.detect_low_tmcs_stock(
        table, tmcs_rows, jst_threshold=flags.threshold, tmcs_threshold=flags.tmcs_threshold
    )
    ctx.state["risk_items"] = risks
    ctx.state["low_stock_count"] = low_count
    ctx.state["low_tmcs_items"] = low_tmcs
    return success_result(
        outputs={
            "low_stock_count": low_count,
            "risk_count": len(risks),
            "low_tmcs_count": len(low_tmcs),
            "risk_items": risks,
            "low_tmcs_items": low_tmcs,
        }
    )


# ── 产物与通知 ────────────────────────────────────────────────────────────────
# 输出表字段（按用户确认）：SKU编码 / 聚水潭实际库存 / 猫超实际库存(可售)。
_OUTPUT_COLUMNS: list[tuple[str, str]] = [
    ("SKU编码", "sku_code"),
    ("商品名称", "product_name"),
    ("聚水潭实际库存", "actual_stock"),
    ("猫超实际库存", "tmcs_total_sellable_stock"),
]


_RISK_SHEET = "库存风险"
_LOW_TMCS_SHEET = "猫超低库存"


def _rows_for(records: list[dict[str, Any]]) -> list[list[Any]]:
    return [[risk.get(key) for _, key in _OUTPUT_COLUMNS] for risk in records]


def _write_output_file(
    path: Path, risks: list[dict[str, Any]], low_tmcs: list[dict[str, Any]]
) -> list[Path]:
    """写主表(库存风险)+子表(猫超低库存)。返回实际写出的文件路径列表。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    headers = [header for header, _ in _OUTPUT_COLUMNS]
    if suffix == ".json":
        payload = {
            _RISK_SHEET: [{h: r.get(k) for h, k in _OUTPUT_COLUMNS} for r in risks],
            _LOW_TMCS_SHEET: [{h: r.get(k) for h, k in _OUTPUT_COLUMNS} for r in low_tmcs],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return [path]
    if suffix == ".csv":
        # csv 无多 sheet：主表写 path，子表写同名 _猫超低库存.csv
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(_rows_for(risks))
        sub_path = path.with_name(f"{path.stem}_{_LOW_TMCS_SHEET}{path.suffix}")
        with sub_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(_rows_for(low_tmcs))
        return [path, sub_path]
    # 默认 xlsx：两个 sheet
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _RISK_SHEET
    sheet.append(headers)
    for row in _rows_for(risks):
        sheet.append(row)
    sub = workbook.create_sheet(_LOW_TMCS_SHEET)
    sub.append(headers)
    for row in _rows_for(low_tmcs):
        sub.append(row)
    workbook.save(path)
    return [path]


def write_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    risks = ctx.state.get("risk_items") or []
    low_tmcs = ctx.state.get("low_tmcs_items") or []
    if not flags.output:
        return success_result(outputs={"output_written": False, "reason": "未指定 --output，仅返回结构化结果"})
    path = Path(flags.output).expanduser()
    written = _write_output_file(path, risks, low_tmcs)
    artifacts = [
        Artifact(
            type=p.suffix.lower().lstrip(".") or "xlsx",
            role="output",
            name=p.name,
            path=str(p),
            platform="tmcs",
            metadata={"risk_count": len(risks), "low_tmcs_count": len(low_tmcs)},
        )
        for p in written
    ]
    return success_result(
        outputs={"output_written": True, "output_paths": [str(p) for p in written]}, artifacts=artifacts
    )


def _build_message(flags: argparse.Namespace, risks: list[dict[str, Any]]) -> str:
    lines = [f"# 猫超库存实时监测预警（{len(risks)} 个低库存SKU）", ""]
    for risk in risks[:30]:
        lines.append(
            f"- SKU {risk['sku_code']}：聚水潭实际库存 {risk['actual_stock']}，"
            f"猫超可售 {risk['tmcs_total_sellable_stock']}"
            f"（专享 {risk['dedicated_sellable_stock']} / 共享 {risk['shared_sellable_stock']}）"
        )
    if len(risks) > 30:
        lines.append(f"... 其余 {len(risks) - 30} 个略")
    return "\n".join(lines)


def notify_if_needed(ctx: StepContext):
    flags = ctx.state["flags"]
    risks = ctx.state.get("risk_items") or []
    if not risks:
        notification = {"sent": False, "reason": "无库存风险，默认不发送通知"}
        ctx.state["notification"] = notification
        return success_result(outputs={"notification": notification})
    message = _build_message(flags, risks)
    ctx.state["warning_message"] = message
    if not flags.notify:
        notification = {"sent": False, "reason": "存在风险但未启用 --notify，仅记录预警", "preview": message}
        ctx.state["notification"] = notification
        return success_result(outputs={"notification": notification})
    notification = send_notification(message, dry_run=flags.dry_run, msgtype="markdown")
    ctx.state["notification"] = notification
    return success_result(outputs={"notification": notification})


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    return success_result(
        outputs={
            "task": "tmcs_realtime_inventory_watch",
            "dry_run": flags.dry_run,
            "jst_rows": len(ctx.state.get("jst_rows") or []),
            "tmcs_stock_rows": len(ctx.state.get("tmcs_rows") or []),
            "active_tmcs_goods_rows": len(ctx.state.get("goods_rows") or []),
            "matched_rows": len(ctx.state.get("inventory_table") or []),
            "low_stock_count": ctx.state.get("low_stock_count", 0),
            "risk_count": len(ctx.state.get("risk_items") or []),
            "low_tmcs_count": len(ctx.state.get("low_tmcs_items") or []),
            "risk_items": ctx.state.get("risk_items") or [],
            "low_tmcs_items": ctx.state.get("low_tmcs_items") or [],
            "warnings": ctx.state.get("warnings") or [],
            "notification": ctx.state.get("notification"),
        }
    )
