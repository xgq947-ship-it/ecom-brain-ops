"""刷单表格登记 workflow 的 step handler。

复用 tasks/append_brush_orders.py 的成熟实现（微信源表识别、多格式解析、去重、登记表
ZIP/XML 追加、聚水潭打标触发）。append 主体逻辑难以无损细拆，按 wrapper 方式整体复用
appender.run()，但保留 step 状态。不重写任何解析/追加/Excel 补丁算法，也不直接请求平台。

dry-run 安全点：
- append_to_register 以 dry_run=True 调用 appender.run()，其中登记表写入、聚水潭
  auth 预检、打标触发、清空源目录均被 `not dry_run` 守卫，dry-run 全部跳过。
- workflow dry-run 允许扫描微信目录并直接读取源文件做预览，但不复制文件、不写登记表。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.append_brush_orders import appender


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--work-dir", default=str(appender.DEFAULT_WORK_DIR))
    parser.add_argument("--source-dir", default=None)
    parser.add_argument("--product-file", default=str(appender.DEFAULT_PRODUCT_FILE))
    parser.add_argument("--brush-product-file", default=str(appender.DEFAULT_BRUSH_PRODUCT_FILE))
    parser.add_argument("--no-auto-fetch-wechat", action="store_true")
    parser.add_argument("--wechat-file-dir", default=str(appender.DEFAULT_WECHAT_FILE_DIR))
    parser.add_argument("--wechat-target-dir", default=str(appender.DEFAULT_WECHAT_TARGET_DIR))
    parser.add_argument("--wechat-date", default=None)
    parser.add_argument("--print-skipped-wechat", action="store_true")
    parser.add_argument("date_words", nargs="*")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    appender.configure_paths(
        work_dir=Path(flags.work_dir),
        source_dir=Path(flags.source_dir) if flags.source_dir else None,
        product_file=Path(flags.product_file),
        brush_product_file=Path(flags.brush_product_file),
        wechat_file_dir=Path(flags.wechat_file_dir),
        wechat_target_dir=Path(flags.wechat_target_dir),
    )
    wechat_month_day = None
    if flags.wechat_date:
        wechat_month_day = appender.parse_month_day(flags.wechat_date)
    elif flags.date_words:
        wechat_month_day = appender.parse_natural_month_day(flags.date_words)
    ctx.state["flags"] = flags
    ctx.state["wechat_month_day"] = wechat_month_day
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "source_dir": str(appender.SOURCE_DIR),
            "auto_fetch_wechat": not flags.no_auto_fetch_wechat,
            "wechat_month_day": list(wechat_month_day) if wechat_month_day else None,
        }
    )


def load_source_orders(ctx: StepContext):
    source_dir = appender.SOURCE_DIR
    has_files = appender.has_xlsx_files(source_dir)
    return success_result(
        outputs={"source_dir": str(source_dir), "has_staged_xlsx": has_files}
    )


def validate_orders(ctx: StepContext):
    flags = ctx.state["flags"]
    product_file = Path(flags.product_file).expanduser()
    brush_file = Path(flags.brush_product_file).expanduser()
    return success_result(
        outputs={
            "product_file_exists": product_file.exists(),
            "brush_product_file_exists": brush_file.exists(),
        }
    )


def append_to_register(ctx: StepContext):
    flags = ctx.state["flags"]
    # dry-run 纯只读：允许扫描微信目录预览，但不写登记表、不预检、不打标、不清源。
    auto_fetch = not flags.no_auto_fetch_wechat
    try:
        summary = appender.run(
            dry_run=flags.dry_run,
            auto_fetch_wechat=auto_fetch,
            wechat_month_day=ctx.state["wechat_month_day"],
            print_skipped_wechat=flags.print_skipped_wechat,
        )
    except (FileNotFoundError, RuntimeError, SystemExit) as exc:
        if flags.dry_run:
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))
    ctx.state["summary"] = summary
    return success_result(
        outputs={
            "appended_count": summary.get("appended_count", 0),
            "source_dir": summary.get("source_dir"),
            "latest_brush_orders_path": summary.get("latest_brush_orders_path"),
        }
    )


def collect_artifacts(ctx: StepContext):
    flags = ctx.state["flags"]
    summary = ctx.state.get("summary") or {}
    artifacts = []
    latest_path = summary.get("latest_brush_orders_path")
    if not flags.dry_run and latest_path and Path(latest_path).exists():
        artifacts.append(
            Artifact(type="json", role="latest_brush_orders", name=Path(latest_path).name, path=str(latest_path))
        )
    return success_result(
        outputs={
            "task": "append_brush_orders",
            "dry_run": flags.dry_run,
            "appended_count": summary.get("appended_count", 0),
            "appended_orders": summary.get("appended_orders", []),
        },
        artifacts=artifacts,
    )
