"""猫超商品信息同步聚水潭 workflow 的 step handler。

商品映射、Excel 生成、平台调用等业务逻辑在同包 cli_client / excel_builder /
input_loader / sync_config，不重写算法，也不直接请求平台（仍经
clients/ops_cli_client.py -> Ops-Cli）。

dry-run 是「安全预览」：只解析输入，不查询真实平台、不生成 Excel、不导入聚水潭。
真实导入必须显式 --import-jst。
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from core.config_loader import get_path
from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.tmcs_sync_jst_shop_goods import cli_client
from workflows.tmcs_sync_jst_shop_goods import excel_builder
from workflows.tmcs_sync_jst_shop_goods import input_loader
from workflows.tmcs_sync_jst_shop_goods import sync_config as skill_config


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--item-ids", default=None)
    parser.add_argument("--input-file", default=None)
    parser.add_argument("--warehouse-code", default=skill_config.DEFAULT_WAREHOUSE_CODE)
    parser.add_argument("--shop-name", default=skill_config.DEFAULT_JST_SHOP_NAME)
    parser.add_argument("--import-mode", default="ignore", choices=["ignore", "cover"])
    parser.add_argument("--import-jst", action="store_true")
    parser.add_argument("--no-import", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "warehouse_code": flags.warehouse_code,
            "shop_name": flags.shop_name,
            "import_mode": flags.import_mode,
            "import_jst": flags.import_jst,
            "has_item_ids": bool(flags.item_ids or flags.input_file),
        }
    )


def load_tmcs_goods(ctx: StepContext):
    flags = ctx.state["flags"]
    if not (flags.item_ids or flags.input_file):
        if ctx.dry_run:
            return success_result(
                outputs={
                    "skipped": True,
                    "reason": "未提供 --item-ids/--input-file；真实执行需要其一",
                    "item_id_count": 0,
                }
            )
        return failure_result("没有输入商品ID。请使用 --item-ids 或 --input-file。")
    item_ids = input_loader.resolve_item_ids(item_ids=flags.item_ids, input_file=flags.input_file)
    ctx.state["item_ids"] = item_ids
    return success_result(outputs={"item_id_count": len(item_ids), "item_ids": item_ids})


def query_tmcs_stock(ctx: StepContext):
    flags = ctx.state["flags"]
    item_ids = ctx.state.get("item_ids") or []
    if ctx.dry_run:
        return success_result(
            outputs={
                "skipped": True,
                "reason": "dry-run 不查询真实平台",
                "would_query_item_ids": len(item_ids),
            }
        )
    if not item_ids:
        return failure_result("没有可查询的商品ID")
    stock_rows = cli_client.query_tmcs_stock(item_ids=item_ids, warehouse_code=flags.warehouse_code)
    ctx.state["stock_rows"] = stock_rows
    return success_result(outputs={"stock_rows": len(stock_rows)})


def build_jst_import_excel(ctx: StepContext):
    flags = ctx.state["flags"]
    if ctx.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run 不生成导入 Excel"})

    item_ids = ctx.state["item_ids"]
    stock_rows = ctx.state["stock_rows"]
    import_rows, failures = excel_builder.build_rows(requested_item_ids=item_ids, stock_rows=stock_rows)
    skill_config.ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workbook_result = excel_builder.build_import_workbooks(
        import_rows=import_rows,
        failures=failures,
        output_dir=skill_config.OUTPUT_DIR,
        timestamp=timestamp,
    )
    ctx.state["workbook_result"] = workbook_result
    ctx.state["import_rows"] = import_rows

    artifacts = [
        Artifact(
            type="xlsx",
            role="import",
            name=Path(workbook_result["import_path"]).name,
            path=str(workbook_result["import_path"]),
            platform="jst",
        )
    ]
    if workbook_result.get("failed_path"):
        artifacts.append(
            Artifact(
                type="xlsx",
                role="failed",
                name=Path(workbook_result["failed_path"]).name,
                path=str(workbook_result["failed_path"]),
                platform="jst",
            )
        )

    # 同步生成「猫超商品对应关系导入表」到桌面：对应商品编码 = 线上商品编码经条码规则匹配聚水潭商品资料得出。
    # 次级产物，失败不阻断主流程（导入文件已就绪），仅在输出里记录错误。
    correspondence_outputs: dict[str, object] = {}
    try:
        correspondence_result = excel_builder.build_correspondence_workbook(
            import_rows=import_rows,
            master_path=get_path("jst_product_file"),
            output_dir=get_path("desktop_dir"),
        )
        ctx.state["correspondence_result"] = correspondence_result
        correspondence_outputs = {
            "correspondence_path": correspondence_result["correspondence_path"],
            "correspondence_rows": correspondence_result["correspondence_rows"],
            "matched_rows": correspondence_result["matched_rows"],
        }
        artifacts.append(
            Artifact(
                type="xlsx",
                role="correspondence",
                name=Path(correspondence_result["correspondence_path"]).name,
                path=str(correspondence_result["correspondence_path"]),
                platform="jst",
            )
        )
    except Exception as exc:  # noqa: BLE001 - 次级产物失败不阻断主同步
        correspondence_outputs = {"correspondence_path": None, "correspondence_error": str(exc)}

    return success_result(
        outputs={
            "import_path": str(workbook_result["import_path"]),
            "failed_path": str(workbook_result["failed_path"]) if workbook_result.get("failed_path") else None,
            "import_rows": workbook_result["import_rows"],
            "failed_rows": workbook_result["failed_rows"],
            **correspondence_outputs,
        },
        artifacts=artifacts,
    )


def import_jst_shop_goods(ctx: StepContext):
    flags = ctx.state["flags"]
    if ctx.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run 不导入聚水潭"})
    if not flags.import_jst:
        return success_result(outputs={"imported": False, "reason": "未启用导入（需 --import-jst）"})

    import_rows = ctx.state.get("import_rows") or []
    if not import_rows:
        return failure_result("没有有效数据可导入聚水潭，已生成失败数据。")
    workbook_result = ctx.state["workbook_result"]
    import_result = cli_client.import_jst_shop_goods(
        file_path=str(workbook_result["import_path"]),
        shop_name=flags.shop_name,
        mode=flags.import_mode,
    )
    ctx.state["import_result"] = import_result
    return success_result(outputs={"imported": True, "import_result": import_result})


def collect_artifacts(ctx: StepContext):
    flags = ctx.state["flags"]
    workbook_result = ctx.state.get("workbook_result") or {}
    correspondence_result = ctx.state.get("correspondence_result") or {}
    return success_result(
        outputs={
            "task": "tmcs_sync_jst_shop_goods",
            "dry_run": flags.dry_run,
            "item_id_count": len(ctx.state.get("item_ids") or []),
            "import_path": str(workbook_result["import_path"]) if workbook_result.get("import_path") else None,
            "correspondence_path": correspondence_result.get("correspondence_path"),
            "import_jst": bool(flags.import_jst),
            "imported": ctx.state.get("import_result") is not None,
        }
    )
