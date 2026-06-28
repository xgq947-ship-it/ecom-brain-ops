"""猫超月账单整理 workflow 的 step handler。

这一层只做「编排」：把既有成熟实现拆成有状态的步骤。
账单解析、Excel 加工、平台下载等业务逻辑在同包 billing.py（动态加载 processor.py 作为
source 模块）+ services/，本文件不重写任何算法，也不直接请求平台（平台动作经
clients/ops_cli_client.py 调 Ops-Cli）。

步骤之间通过 ctx.state 传递 Python 对象（source 模块、bill_files、计算好的行等）。
dry-run 下不触发任何真实下载与文件写入。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.runtime import Artifact, StepContext, failure_result, success_result

from workflows.tmall_monthly_bill import billing
from workflows.tmall_monthly_bill.services.profit_summary_service import render_profit_summary
from workflows.tmall_monthly_bill.services.promotion_service import write_promotion_sheet
from workflows.tmall_monthly_bill.services.reconciliation_service import write_reconciliation_sheet


def _build_args(ctx: StepContext) -> SimpleNamespace:
    """从 workflow inputs 构造与 billing.process() 期望同名的参数对象。"""
    args = ctx.inputs.get("args") or []
    return SimpleNamespace(
        bill_dir=str(billing.DEFAULT_BILL_DIR),
        work_dir=str(billing.DEFAULT_WORK_DIR),
        source_script=str(billing.INTERNAL_SOURCE_SCRIPT),
        statement_list=str(billing.DEFAULT_STATEMENT_LIST),
        table1_file=str(billing.DEFAULT_TABLE1_FILE),
        table2_file=str(billing.DEFAULT_TABLE2_FILE),
        downloader_script=str(billing.INTERNAL_DOWNLOADER_SCRIPT),
        skip_auto_download="--skip-auto-download" in args,
        dry_run=ctx.dry_run,
    )


def check_inputs(ctx: StepContext):
    args = _build_args(ctx)
    bill_dir = Path(args.bill_dir).expanduser().resolve()
    work_dir = Path(args.work_dir).expanduser().resolve()
    output_dir = billing.DEFAULT_OUTPUT_DIR.expanduser().resolve()
    source_script = Path(args.source_script).expanduser().resolve()
    statement_path = Path(args.statement_list).expanduser().resolve()
    table1_path = billing.resolve_configured_file(args.table1_file, work_dir)
    table2_path = billing.resolve_configured_file(args.table2_file, work_dir)

    missing = []
    if not bill_dir.is_dir():
        missing.append(f"HDB 账单目录不存在：{bill_dir}")
    if not work_dir.is_dir():
        missing.append(f"工作区不存在：{work_dir}")
    if not source_script.is_file():
        missing.append(f"账单整理脚本不存在：{source_script}")
    if missing:
        return failure_result(missing)

    ctx.state.update(
        args=args,
        bill_dir=bill_dir,
        work_dir=work_dir,
        output_dir=output_dir,
        source_script=source_script,
        statement_path=statement_path,
        table1_path=table1_path,
        table2_path=table2_path,
    )
    return success_result(
        outputs={
            "bill_dir": str(bill_dir),
            "work_dir": str(work_dir),
            "output_dir": str(output_dir),
            "table1_exists": table1_path.exists(),
            "table2_exists": table2_path.exists(),
        }
    )


def check_local_sources(ctx: StepContext):
    state = ctx.state
    source = billing.load_source_module(state["source_script"])
    state["source"] = source

    bill_files = billing.find_hdb_files(state["bill_dir"])
    state["bill_files"] = bill_files

    month = None
    statement_found = None
    if bill_files:
        try:
            month = source.infer_month_from_bills(bill_files)
        except Exception:  # noqa: BLE001 - 文件名异常时月份待后续步骤再定
            month = None
        periods = billing.infer_bill_periods(bill_files)
        matched = billing.find_matching_statement_list(source, state["statement_path"], periods)
        statement_found = str(matched) if matched else None
        if matched:
            state["statement_path"] = matched
    if month is None and ctx.inputs.get("month"):
        # --month 形如 YYYY-MM，仅用于报告；真实月份仍以 HDB 文件名为准。
        month = str(ctx.inputs["month"])
    state["month"] = month

    return success_result(
        outputs={
            "bill_file_count": len(bill_files),
            "bill_files": [str(path) for path in bill_files],
            "month": month,
            "matching_statement_list": statement_found,
        }
    )


def download_tmcs_bill(ctx: StepContext):
    state = ctx.state
    if ctx.dry_run:
        have = bool(state["bill_files"])
        return success_result(
            outputs={
                "skipped": True,
                "reason": "本地已有 HDB，dry-run 不重复下载" if have else "dry-run 不触发真实平台下载",
                "local_bill_count": len(state["bill_files"]),
            }
        )

    args = state["args"]
    source = state["source"]
    auto_download_info = billing.auto_download_bills_if_needed(args, state["bill_dir"])
    bill_files = source.get_bill_files(state["bill_dir"])
    state["bill_files"] = bill_files
    statement_info = billing.auto_download_statement_list_if_needed(
        args, source, state["statement_path"], bill_files, state["bill_dir"]
    )
    state["auto_download_info"] = auto_download_info
    state["statement_download_info"] = statement_info
    return success_result(
        outputs={
            "bill_file_count": len(bill_files),
            **auto_download_info,
            **statement_info,
        }
    )


def download_promotion_bill(ctx: StepContext):
    state = ctx.state
    if ctx.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run 不触发推广账单下载"})

    bill_files = state["bill_files"]
    start, end = billing.infer_bill_date_range(bill_files)
    wxt_path = billing.download_promotion_bill("wxt", start, end, state["bill_dir"])
    zdx_path = billing.download_promotion_bill("zdx", start, end, state["bill_dir"])
    state["period_start"] = start
    state["period_end"] = end
    state["promotion_paths"] = {"wxt": str(wxt_path), "zdx": str(zdx_path)}
    state["wxt_path"] = wxt_path
    state["zdx_path"] = zdx_path
    artifacts = [
        Artifact(type="csv", role="promotion_source", name=Path(wxt_path).name, path=str(wxt_path), platform="tmcs", month=state.get("month") or ""),
        Artifact(type="xlsx", role="promotion_source", name=Path(zdx_path).name, path=str(zdx_path), platform="tmcs", month=state.get("month") or ""),
    ]
    return success_result(outputs={"promotion_bill_files": state["promotion_paths"]}, artifacts=artifacts)


def validate_sources(ctx: StepContext):
    state = ctx.state
    source = state["source"]
    bill_files = state["bill_files"]

    if ctx.dry_run and not bill_files:
        return success_result(
            outputs={"skipped": True, "reason": "dry-run 且本地无 HDB，无法校验；真实执行时会先下载再校验"}
        )

    if not bill_files:
        return failure_result("未找到 HDB 账单文件，无法构建账单数据")

    table1_path = state["table1_path"]
    table2_path = state["table2_path"]
    if not table1_path.exists():
        return failure_result(f"未找到猫超商品列表主表：{table1_path}")
    if not table2_path.exists():
        return failure_result(f"未找到聚水潭商品资料：{table2_path}")

    month = source.infer_month_from_bills(bill_files)
    state["month"] = month

    raw_header, raw_rows = source.build_combined_rows(bill_files)
    table1_mapping = source.build_table1_mapping(table1_path)
    table2_mapping = source.build_table2_mapping(table2_path)
    enriched_header, enriched_rows, stats = source.enrich_rows(raw_header, raw_rows, table1_mapping, table2_mapping)
    main_rows_sorted = source.sort_rows_by_backend_code(raw_header, raw_rows)
    cargo_rows = source.build_sub_sheet_rows(enriched_header, enriched_rows, "货款表格")
    ticket_rows = source.build_sub_sheet_rows(enriched_header, enriched_rows, "票扣表格")
    charge_rows = source.build_sub_sheet_rows(raw_header, raw_rows, "账扣表格")
    invoice_header, invoice_rows, price_conflict_codes = source.build_invoice_sheet(
        enriched_header, cargo_rows, enriched_header, ticket_rows
    )
    cost_header, cost_rows = source.build_cost_sheet(enriched_header, cargo_rows)
    invoice_check = billing.compare_invoice_amount(
        source, state["statement_path"], invoice_header, invoice_rows, bill_files
    )

    state.update(
        raw_header=raw_header,
        raw_rows=raw_rows,
        enriched_header=enriched_header,
        main_rows_sorted=main_rows_sorted,
        cargo_rows=cargo_rows,
        ticket_rows=ticket_rows,
        charge_rows=charge_rows,
        invoice_header=invoice_header,
        invoice_rows=invoice_rows,
        cost_header=cost_header,
        cost_rows=cost_rows,
        price_conflict_codes=price_conflict_codes,
        invoice_check=invoice_check,
        enrich_stats=stats,
    )
    return success_result(
        outputs={
            "month": month,
            "total_rows": len(raw_rows),
            "invoice_rows": len(invoice_rows),
            "cost_rows": len(cost_rows),
            "price_conflict_codes": price_conflict_codes,
            "invoice_amount_check": invoice_check.get("invoice_amount_check"),
            "statement_list": invoice_check.get("statement_list"),
        }
    )


def process_excel(ctx: StepContext):
    state = ctx.state
    source = state["source"]
    month = state.get("month")
    output_dir = state["output_dir"]
    output_path = output_dir / source.OUTPUT_TEMPLATE.format(month=month) if month else None

    if ctx.dry_run:
        return success_result(
            outputs={
                "skipped": True,
                "reason": "dry-run 不写最终账单文件",
                "planned_output_file": str(output_path) if output_path else None,
            }
        )

    main_sheet_name = source.MAIN_SHEET_TEMPLATE.format(month=month)
    output_dir.mkdir(parents=True, exist_ok=True)
    source.ensure_clean_target(output_path)

    invoice_check = state["invoice_check"]
    workbook = source.Workbook()
    workbook.remove(workbook.active)
    source.append_sheet(workbook, main_sheet_name, state["raw_header"], state["main_rows_sorted"])
    source.append_sheet(workbook, "货款表格", state["enriched_header"], state["cargo_rows"])
    source.append_sheet(workbook, "票扣表格", state["enriched_header"], state["ticket_rows"])
    source.append_sheet(workbook, "账扣表格", state["raw_header"], state["charge_rows"])
    source.append_sheet(workbook, "开票表", state["invoice_header"], state["invoice_rows"])
    source.append_sheet(workbook, "成本表", state["cost_header"], state["cost_rows"])
    write_reconciliation_sheet(
        workbook,
        Path(invoice_check["statement_list"]),
        period_start=state["period_start"],
        period_end=state["period_end"],
    )
    write_promotion_sheet(
        workbook,
        "万相台推广数据表格",
        state["wxt_path"],
        period_start=state["period_start"],
        period_end=state["period_end"],
    )
    write_promotion_sheet(
        workbook,
        "智多星推广数据表格",
        state["zdx_path"],
        period_start=state["period_start"],
        period_end=state["period_end"],
    )
    render_profit_summary(
        workbook,
        month_label=f"{month}月份利润表",
        period_start=state["period_start"],
        period_end=state["period_end"],
    )
    workbook.save(output_path)

    state["output_path"] = output_path
    artifact = Artifact(
        type="xlsx",
        role="output",
        name=output_path.name,
        path=str(output_path),
        platform="tmcs",
        month=str(month),
    )
    return success_result(outputs={"output_file": str(output_path)}, artifacts=[artifact])


def collect_artifacts(ctx: StepContext):
    state = ctx.state
    month = state.get("month")
    artifacts: list[Artifact] = []
    for path in state.get("bill_files", []):
        artifacts.append(
            Artifact(type="xlsx", role="hdb_source", name=Path(path).name, path=str(path), platform="tmcs", month=str(month or ""))
        )
    statement_path = state.get("statement_path")
    if statement_path and Path(statement_path).exists():
        artifacts.append(
            Artifact(type="xlsx", role="statement_list", name=Path(statement_path).name, path=str(statement_path), platform="tmcs", month=str(month or ""))
        )

    output_path = state.get("output_path")
    summary: dict[str, Any] = {
        "task": "process_maochao_bills",
        "workflow": "tmall_monthly_bill",
        "dry_run": ctx.dry_run,
        "month": month,
        "bill_file_count": len(state.get("bill_files", [])),
        "output_file": str(output_path) if output_path else None,
        "output_written": output_path is not None,
        "promotion_bill_files": state.get("promotion_paths", {}),
    }
    invoice_check = state.get("invoice_check")
    if invoice_check:
        summary["invoice_amount_check"] = invoice_check.get("invoice_amount_check")
    return success_result(outputs=summary, artifacts=artifacts)
