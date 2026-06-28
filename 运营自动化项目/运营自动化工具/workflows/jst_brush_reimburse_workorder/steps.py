"""聚水潭刷单报销工单 workflow 的 step handler。

复用 tasks/jst_brush_reimburse_workorder.py 的全部成熟实现（批次读取、候选核验、
Ops-Cli 报销调用、备份、标记行 ZIP/XML 补丁、失败导出），不重写任何业务算法，
也不直接请求平台（仍经 clients/ops_cli_client.py -> Ops-Cli）。

dry-run 安全点（关键）：
- submit_workorder（execute=True 真实提交工单）在 dry-run 下跳过。
- update_register（backup_workbook + write_marker_row 改写登记表）在 dry-run 下跳过，
  因此登记表及其图片/DISPIMG/cellimages 结构在 dry-run 下绝不被改写。
- dry-run 不写失败导出文件。
预览阶段的候选核验只做 execute=False 的只读查询（与旧任务一致）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.jst_brush_reimburse_workorder import reimburse


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--input", default=None)
    parser.add_argument("--order-no", default=None)
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    if not namespace.input:
        from datetime import datetime

        namespace.input = str(
            reimburse.DEFAULT_WORKBOOK_DIR / reimburse.DEFAULT_WORKBOOK_TEMPLATE.format(month=datetime.now().month)
        )
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    workbook_path = Path(flags.input).expanduser().resolve()
    ctx.state["flags"] = flags
    ctx.state["workbook_path"] = workbook_path
    if not workbook_path.exists():
        if flags.dry_run:
            ctx.state["no_input"] = True
            return success_result(
                outputs={"skipped": True, "reason": f"登记表不存在：{workbook_path}（dry-run 安全预览）"}
            )
        return failure_result(f"找不到刷单登记表：{workbook_path}")
    return success_result(
        outputs={"dry_run": flags.dry_run, "input": str(workbook_path), "order_no": flags.order_no}
    )


def load_reimburse_data(ctx: StepContext):
    if ctx.state.get("no_input"):
        return success_result(outputs={"skipped": True, "reason": "无登记表，跳过批次读取"})
    workbook_path = ctx.state["workbook_path"]
    try:
        batch = reimburse.read_current_batch(workbook_path)
    except (FileNotFoundError, RuntimeError) as exc:
        if ctx.state["flags"].dry_run:
            ctx.state["no_batch"] = True
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))
    ctx.state["batch"] = batch
    return success_result(
        outputs={
            "start_row": batch.start_row,
            "end_row": batch.end_row,
            "order_count": len(batch.orders),
        }
    )


def validate_amounts(ctx: StepContext):
    batch = ctx.state.get("batch")
    if batch is None:
        return success_result(outputs={"skipped": True, "reason": "无批次数据"})
    return success_result(
        outputs={
            "principal_total": reimburse.money_text(batch.principal_total),
            "payout_total": reimburse.money_text(batch.payout_total),
            "order_count": len(batch.orders),
        }
    )


def preview_workorder(ctx: StepContext):
    batch = ctx.state.get("batch")
    if batch is None:
        return success_result(outputs={"skipped": True, "reason": "无批次数据"})
    flags = ctx.state["flags"]
    candidate, checked = reimburse.choose_candidate(
        batch,
        order_no=flags.order_no,
        interactive_recovery=not flags.dry_run,
    )
    ctx.state["candidate"] = candidate
    ctx.state["checked"] = checked
    skipped = [
        {"order_no": item.order.order_no, "reason": item.skip_reason or "已存在报销工单"}
        for item in checked
        if item.skip_reason or item.has_existing_workorder
    ]
    ctx.state["skipped"] = skipped
    return success_result(
        outputs={
            "candidate_order_no": candidate.order.order_no if candidate else None,
            "lp_order_no": candidate.lp_order_no if candidate else None,
            "has_candidate": candidate is not None,
            "skipped": skipped,
        }
    )


def submit_workorder(ctx: StepContext):
    flags = ctx.state["flags"]
    if flags.dry_run:
        return success_result(
            outputs={"skipped": True, "reason": "dry-run 不提交真实工单（不执行 execute）"}
        )
    batch = ctx.state.get("batch")
    candidate = ctx.state.get("candidate")
    if batch is None:
        return failure_result("无批次数据，无法创建工单")
    if candidate is None:
        return success_result(
            outputs={"submitted": False, "reason": "当前批次无可创建工单的订单（均已存在或核验失败）"}
        )
    create_result = reimburse.ops_reimburse_payload(batch, candidate.order, execute=True, interactive_recovery=True)
    ctx.state["create_result"] = create_result
    if create_result.get("has_existing_workorder") and not create_result.get("submitted"):
        return success_result(outputs={"submitted": False, "reason": "创建前复核发现工单已存在"})
    return success_result(
        outputs={
            "submitted": True,
            "upload_url": reimburse.cell_text(create_result.get("upload_url")),
            "result": create_result.get("result"),
        }
    )


def update_register(ctx: StepContext):
    flags = ctx.state["flags"]
    if flags.dry_run:
        return success_result(
            outputs={"skipped": True, "reason": "dry-run 不备份、不写标记行（登记表零改写）"}
        )
    create_result = ctx.state.get("create_result") or {}
    if not create_result.get("submitted"):
        return success_result(outputs={"skipped": True, "reason": "未提交工单，无需回写登记表"})
    batch = ctx.state["batch"]
    backup_path = reimburse.backup_workbook(ctx.state["workbook_path"])
    marker_row = reimburse.write_marker_row(batch)
    ctx.state["backup_path"] = backup_path
    ctx.state["marker_row"] = marker_row
    return success_result(
        outputs={"backup_path": str(backup_path), "marker_row": marker_row},
        artifacts=[
            Artifact(type="xlsx", role="register_backup", name=Path(backup_path).name, path=str(backup_path))
        ],
    )


def collect_artifacts(ctx: StepContext):
    flags = ctx.state["flags"]
    failures = [
        reimburse.FailureRecord(order_no=item["order_no"], reason=item["reason"])
        for item in ctx.state.get("skipped", [])
        if item.get("reason")
    ]
    failed_export = None
    artifacts = []
    if not flags.dry_run and failures:
        # dry-run 不写任何文件；仅真实执行时导出失败记录。
        failed_export = reimburse.write_failed_export(failures)
        if failed_export:
            artifacts.append(
                Artifact(type="xlsx", role="failed", name=Path(failed_export).name, path=str(failed_export))
            )
    return success_result(
        outputs={
            "task": "jst_brush_reimburse_workorder",
            "dry_run": flags.dry_run,
            "submitted": bool((ctx.state.get("create_result") or {}).get("submitted")),
            "marker_row": ctx.state.get("marker_row"),
            "backup_path": str(ctx.state["backup_path"]) if ctx.state.get("backup_path") else None,
            "failed_export": str(failed_export) if failed_export else None,
        },
        artifacts=artifacts,
    )
