"""买家秀自动分组、压缩与登记表回写 workflow 的 step handler。

复用同包 packager.py 的全部成熟实现（登记表匹配、分组轮询、zip 打包、登记表
ZIP/XML 回写、轮询游标），不重写任何业务算法或 Excel 补丁逻辑。

dry-run 安全点：
- build_zip_packages 在 dry-run 跳过：不打包、不复制图片（旧任务也只复制不移动原图）。
- update_register 在 dry-run 跳过：不回写登记表（图片/DISPIMG/cellimages 结构零改写）。
- dry-run 不重置、不推进轮询游标，轮询状态文件零改写。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from core.config_loader import get_path
from workflows.buyer_show import packager


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--buyer-show-path", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--workbook", default=None)
    parser.add_argument("--groups", default=None)
    parser.add_argument("--batch", default=None)
    parser.add_argument("--images-per-group", type=int, default=5)
    parser.add_argument("--allow-total-shortage", type=int, default=0)
    parser.add_argument("--desktop", default=None)
    parser.add_argument("--reset-rotation", action="store_true")
    parser.add_argument("--rotation-key", default=None)
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags
    # 买家秀路径是唯一硬性必填项；型号缺省时从登记表自动推断（见下）。
    if not flags.buyer_show_path:
        if flags.dry_run:
            ctx.state["no_input"] = True
            return success_result(
                outputs={"skipped": True, "reason": "缺少 --buyer-show-path（dry-run 安全预览）"}
            )
        return failure_result("缺少必填参数：--buyer-show-path")

    base = Path(flags.buyer_show_path).expanduser()
    if not base.is_dir():
        if flags.dry_run:
            ctx.state["no_input"] = True
            return success_result(outputs={"skipped": True, "reason": f"买家秀路径不存在：{base}"})
        return failure_result(f"买家秀路径不存在：{base}")

    try:
        workbook = Path(flags.workbook).expanduser() if flags.workbook else packager.latest_workbook()
    except SystemExit as exc:
        if flags.dry_run:
            ctx.state["no_input"] = True
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))

    # 只给买家秀路径、未指定 --model 时：自动从登记表推断默认型号
    # （第一条「买家秀是否自动生成」为空的订单的「名称」整串）。
    model_auto_detected = False
    if not flags.model:
        try:
            flags.model = packager.detect_default_model(workbook)
            model_auto_detected = True
        except SystemExit as exc:
            if flags.dry_run:
                ctx.state["no_input"] = True
                return success_result(outputs={"skipped": True, "reason": str(exc)})
            return failure_result(str(exc))
    ctx.state["model_auto_detected"] = model_auto_detected

    desktop = Path(flags.desktop).expanduser() if flags.desktop else Path(get_path("buyer_show_output_dir"))
    rotation_key = flags.rotation_key or packager.default_rotation_key(base, flags.model, flags.batch)
    ctx.state.update(base=base, workbook=workbook, desktop=desktop, rotation_key=rotation_key)
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "buyer_show_path": str(base),
            "workbook": str(workbook),
            "model": flags.model,
            "model_auto_detected": model_auto_detected,
            "rotation_key": rotation_key,
        }
    )


def scan_buyer_show_sources(ctx: StepContext):
    if ctx.state.get("no_input"):
        return success_result(outputs={"skipped": True, "reason": "无有效输入"})
    flags = ctx.state["flags"]
    try:
        records, product_name, ci, summary = packager.read_matches(ctx.state["workbook"], flags.model)
    except SystemExit as exc:
        if flags.dry_run:
            ctx.state["no_records"] = True
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))
    ctx.state.update(records=records, product_name=product_name, ci=ci, summary=summary)
    return success_result(
        outputs={
            "product_name": product_name,
            "matched_records": len(records),
            "pending_date_keys": summary["pending_date_keys"],
            "skipped_generated_count": summary["skipped_generated_count"],
        }
    )


def select_groups(ctx: StepContext):
    if ctx.state.get("no_input") or ctx.state.get("no_records"):
        return success_result(outputs={"skipped": True, "reason": "无可分组的数据"})
    flags = ctx.state["flags"]

    # dry-run 不重置轮询游标（保持轮询状态零改写）。
    if flags.reset_rotation and not flags.groups and not flags.dry_run:
        packager.reset_rotation_cursor(ctx.state["rotation_key"])

    try:
        batch_plan, rotation_meta = packager.select_group_batches(
            base=ctx.state["base"],
            records=ctx.state["records"],
            groups_arg=flags.groups,
            batch=flags.batch,
            images_per_group=flags.images_per_group,
            allow_total_shortage=flags.allow_total_shortage,
            rotation_key=ctx.state["rotation_key"],
        )
    except SystemExit as exc:
        if flags.dry_run:
            ctx.state["cannot_execute"] = True
            return success_result(outputs={"can_execute": False, "failure_reason": str(exc)})
        return failure_result(str(exc))

    ctx.state.update(batch_plan=batch_plan, rotation_meta=rotation_meta)
    return success_result(
        outputs={
            "can_execute": True,
            "source_mode": rotation_meta["source_mode"],
            "rotation_cursor_before": rotation_meta["rotation_cursor_before"],
            "rotation_cursor_after": rotation_meta["rotation_cursor_after"],
            "batches": [
                {
                    "date_key": batch["date_key"],
                    "order_ids": [record["order_id"] for record in batch["records"]],
                    "groups": [name for name, _ in batch["groups"]],
                }
                for batch in batch_plan
            ],
        }
    )


def build_zip_packages(ctx: StepContext):
    flags = ctx.state["flags"]
    if flags.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run 不打包、不复制图片"})
    if ctx.state.get("no_input") or ctx.state.get("no_records") or ctx.state.get("cannot_execute"):
        return failure_result("无法生成买家秀压缩包：数据或分组不足")

    product_name = ctx.state["product_name"]
    zip_outputs = []
    artifacts = []
    for planned_batch in ctx.state["batch_plan"]:
        assignments = list(zip(planned_batch["records"], planned_batch["groups"]))
        for brusher, items in packager.bucket_assignments_by_brusher(assignments):
            bucket_records = [record for record, _ in items]
            bucket_groups = [group for _, group in items]
            zip_path, manifest = packager.package_zip(
                bucket_records, product_name, flags.model, bucket_groups, ctx.state["desktop"], flags.images_per_group
            )
            counts = packager.verify_zip(zip_path, bucket_records, product_name, flags.images_per_group, flags.allow_total_shortage)
            zip_outputs.append(
                {
                    "date_key": planned_batch["date_key"],
                    "brusher": brusher,
                    "zip_path": str(zip_path),
                    "matched_records": len(bucket_records),
                    "groups": [name for name, _ in bucket_groups],
                    "zip_counts": counts,
                }
            )
            artifacts.append(
                Artifact(type="zip", role="buyer_show_package", name=Path(zip_path).name, path=str(zip_path))
            )
    ctx.state["zip_outputs"] = zip_outputs
    return success_result(outputs={"zip_count": len(zip_outputs), "zip_outputs": zip_outputs}, artifacts=artifacts)


def update_register(ctx: StepContext):
    flags = ctx.state["flags"]
    if flags.dry_run:
        return success_result(
            outputs={"skipped": True, "reason": "dry-run 不回写登记表、不推进轮询"}
        )
    _backup, verify = packager.patch_workbook(ctx.state["workbook"], ctx.state["records"], ctx.state["ci"])
    rotation_meta = ctx.state["rotation_meta"]
    if rotation_meta["source_mode"] == "grouped" and not flags.groups:
        packager.set_rotation_cursor(
            rotation_key=ctx.state["rotation_key"],
            cursor=rotation_meta["rotation_cursor_after"],
            base=ctx.state["base"],
            model=flags.model,
            batch=flags.batch,
            group_names=[name for name, _ in packager.grouped_sources(ctx.state["base"], batch=flags.batch)],
        )
    return success_result(
        outputs={"workbook_verify": verify},
    )


def collect_artifacts(ctx: StepContext):
    flags = ctx.state["flags"]
    return success_result(
        outputs={
            "task": "buyer_show",
            "dry_run": flags.dry_run,
            "matched_records": len(ctx.state.get("records") or []),
            "zip_outputs": ctx.state.get("zip_outputs", []),
            "rotation_key": ctx.state.get("rotation_key"),
        }
    )
