"""公司网盘下载产品 workflow 的 step handler。

选材/匹配/复制/Excel 等纯业务逻辑在同包 listing.py，NAS 挂载来自
workflows.company_nas_common.nas；本层只做编排，不重写任何选材/匹配算法，
也不移动/删除 NAS 文件。

dry-run 安全点：
- copy_product_assets 在 dry-run 跳过：不复制/移动任何文件。
- build_listing_data 在 dry-run 跳过：不生成/覆盖上架数据 Excel。
- 仅做只读的源目录定位与素材计数预览；收尾按既有口径卸载 NAS。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.company_nas_listing import listing


def _build_args(ctx: StepContext) -> SimpleNamespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--text", default=None)
    parser.add_argument("--brand", default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument("--models", nargs="*", default=[])
    parser.add_argument("--models-file", default=None)
    parser.add_argument("--target-root", default=None)
    parser.add_argument("--jst-workbook", default=str(listing.JST_WORKBOOK))
    parser.add_argument("--include-buyer-show", action="store_true")
    parser.add_argument("--keep-mounted", action="store_true")
    parser.add_argument("--no-replace", action="store_true")
    parser.add_argument("--skip-excel", action="store_true")
    ns = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    ns.dry_run = ctx.dry_run or ns.dry_run
    return ns


def check_inputs(ctx: StepContext):
    args = _build_args(ctx)
    ctx.state["args"] = args
    try:
        listing.resolve_args(args)  # 校验 brand/category/models 是否齐全
    except SystemExit as exc:
        if args.dry_run:
            ctx.state["no_input"] = True
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))
    return success_result(
        outputs={
            "dry_run": args.dry_run,
            "brand": args.brand,
            "category": args.category,
            "models": list(args.models),
            "include_buyer_show": args.include_buyer_show,
        }
    )


def parse_listing_request(ctx: StepContext):
    if ctx.state.get("no_input"):
        return success_result(outputs={"skipped": True, "reason": "参数不足"})
    args = ctx.state["args"]
    try:
        specs = listing.load_models(args)
    except SystemExit as exc:
        if args.dry_run:
            ctx.state["no_input"] = True
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))
    ctx.state["specs"] = specs
    return success_result(outputs={"model_count": len(specs), "models": [s.path_text for s in specs]})


def search_nas_index(ctx: StepContext):
    if ctx.state.get("no_input"):
        return success_result(outputs={"skipped": True, "reason": "参数不足"})
    args = ctx.state["args"]
    ctx.state["mounted_before"] = listing.is_mounted()
    try:
        listing.mount_nas()
        index_payload = listing.load_nas_index()
        freshness = listing.index_freshness(index_payload)
        source_base = listing.brand_source_dir(args.brand, args.category)
        if not source_base.is_dir() and not index_payload:
            raise SystemExit(f"源类目目录不存在：{source_base}")
        target_base = listing.target_base_dir(args.brand, args.category, args.target_root)
        plan = []
        for spec in ctx.state["specs"]:
            display, src, resolver = listing.indexed_model_source(args.brand, args.category, source_base, spec.path_text)
            _, dst = listing.model_target(target_base, spec.path_text)
            if not src.is_dir():
                plan.append({"spec": spec, "display": display, "src": src, "dst": dst, "resolver": resolver, "files": [], "status": "源目录不存在", "select_source": "none"})
                continue
            # 选材优先走全量索引（零网络），命中失败/索引过期回退实时遍历。
            files, select_source = listing.select_files_resolved(src, args.include_buyer_show, index_payload)
            plan.append({"spec": spec, "display": display, "src": src, "dst": dst, "resolver": resolver, "files": files, "status": "ok", "select_source": select_source})
    except SystemExit as exc:
        if args.dry_run:
            ctx.state["scan_failed"] = True
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))
    ctx.state["plan"] = plan
    ctx.state["target_base"] = target_base
    warnings = []
    if freshness.get("stale"):
        warnings.append(
            "索引可能过期或未建全量索引，已对未命中项回退实时遍历；"
            "如需加速请先运行：更新公司网盘索引 --include-files --max-depth 0"
        )
    return success_result(
        outputs={
            "target_base": str(target_base),
            "index": freshness,
            "warnings": warnings,
            "items": [
                {"model": item["display"], "source": str(item["src"]), "target": str(item["dst"]), "selected_files": len(item["files"]), "select_source": item["select_source"], "status": item["status"]}
                for item in plan
            ],
        }
    )


def copy_product_assets(ctx: StepContext):
    args = ctx.state["args"]
    if args.dry_run or ctx.state.get("no_input") or ctx.state.get("scan_failed"):
        return success_result(outputs={"skipped": True, "reason": "dry-run 或无可复制项：不复制/移动任何文件"})
    target_dirs = []
    results = []
    for item in ctx.state["plan"]:
        if item["status"] != "ok":
            results.append({"model": item["display"], "copied_files": 0, "status": item["status"]})
            continue
        copied, missing = listing.copy_product(item["src"], item["dst"], item["files"], replace=not args.no_replace, dry_run=False)
        target_dirs.append(item["dst"])
        results.append({"model": item["display"], "copied_files": copied, "missing_files": missing, "status": "ok"})
    ctx.state["target_dirs"] = target_dirs
    return success_result(outputs={"copied": results})


def build_listing_data(ctx: StepContext):
    args = ctx.state["args"]
    if args.dry_run or args.skip_excel or ctx.state.get("no_input") or ctx.state.get("scan_failed"):
        return success_result(outputs={"skipped": True, "reason": "dry-run/skip-excel：不生成上架 Excel"})
    jst_headers, jst_rows = listing.load_jst_rows(Path(args.jst_workbook).expanduser())
    listing_files = []
    for item in ctx.state["plan"]:
        if item["status"] != "ok":
            continue
        spec = item["spec"]
        match, remark = listing.match_jst(item["display"], spec.manual_code, jst_headers, jst_rows)
        row = listing.listing_row(item["display"], args.brand, args.category, match, remark, jst_headers)
        listing_path = item["dst"] / "上架数据.xlsx"
        listing.save_listing(listing_path, [row], f"{item['display']} 上架数据")
        listing_files.append(listing_path)
    ctx.state["listing_files"] = listing_files
    return success_result(outputs={"listing_workbooks": [str(p) for p in listing_files]})


def collect_artifacts(ctx: StepContext):
    args = ctx.state["args"]
    validation = {}
    if not args.dry_run and not args.skip_excel and not ctx.state.get("no_input") and not ctx.state.get("scan_failed"):
        validation = listing.validate_outputs(ctx.state.get("target_dirs", []), ctx.state.get("listing_files", []), args.include_buyer_show)

    # 收尾卸载 NAS（非 keep-mounted 且仍挂载时卸载）。
    if not args.keep_mounted and listing.is_mounted() and not ctx.state.get("mounted_before"):
        try:
            listing.unmount_nas()
        except Exception:  # noqa: BLE001
            pass

    artifacts = []
    for path in ctx.state.get("listing_files", []) or []:
        if Path(path).exists():
            artifacts.append(Artifact(type="xlsx", role="listing", name=Path(path).name, path=str(path)))

    return success_result(
        outputs={
            "task": "company_nas_listing",
            "dry_run": args.dry_run,
            "brand": args.brand,
            "category": args.category,
            "listing_workbooks": [str(p) for p in ctx.state.get("listing_files", [])],
            "validation": validation,
        },
        artifacts=artifacts,
    )
