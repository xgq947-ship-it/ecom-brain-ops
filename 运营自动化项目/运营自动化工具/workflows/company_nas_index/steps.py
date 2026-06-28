"""更新公司网盘索引 workflow 的 step handler。

索引扫描/汇总/写出/搜索打分等纯业务逻辑在同包 indexer.py；NAS 挂载与产品资料根目录
来自 workflows.company_nas_common.nas。本层只做编排，不重写算法，也不移动 NAS 文件。

dry-run 安全点：
- save_index 在 dry-run 跳过：不写正式索引文件（JSON/MD/CSV），不覆盖现有索引。
- 扫描为只读遍历，不移动/删除 NAS 文件。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.company_nas_common import nas
from workflows.company_nas_index import indexer


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", default=None)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--include-files", action="store_true")
    parser.add_argument("--keep-mounted", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags
    ctx.state["mode"] = "search" if flags.query else "build"
    return success_result(
        outputs={"dry_run": flags.dry_run, "mode": ctx.state["mode"], "query": flags.query, "max_depth": flags.max_depth}
    )


def scan_nas(ctx: StepContext):
    flags = ctx.state["flags"]
    if ctx.state["mode"] == "search":
        try:
            result = indexer.search_index(flags.query, limit=flags.limit)
        except SystemExit as exc:
            if flags.dry_run:
                return success_result(outputs={"skipped": True, "reason": str(exc)})
            return failure_result(str(exc))
        ctx.state["search_result"] = result
        return success_result(outputs={"match_count": result.get("match_count", 0)})

    should_mount = flags.root is None
    ctx.state["should_mount"] = should_mount
    ctx.state["mounted_before"] = nas.active_nas_mount() is not None
    try:
        if should_mount:
            nas.mount_nas()
        root = Path(flags.root).expanduser() if flags.root else nas.nas_product_root()
        records = indexer.scan_index(root, max_depth=flags.max_depth, include_files=flags.include_files)
    except SystemExit as exc:
        if flags.dry_run:
            ctx.state["scan_failed"] = True
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))
    ctx.state["root"] = root
    ctx.state["records"] = records
    return success_result(outputs={"root": str(root), "record_count": len(records)})


def build_index(ctx: StepContext):
    if ctx.state["mode"] == "search" or ctx.state.get("scan_failed"):
        return success_result(outputs={"skipped": True, "reason": "搜索模式或扫描未完成"})
    summary = indexer.summarize(ctx.state["records"])
    ctx.state["summary"] = summary
    return success_result(outputs={"summary": summary})


def save_index(ctx: StepContext):
    flags = ctx.state["flags"]
    if ctx.state["mode"] == "search" or ctx.state.get("scan_failed"):
        return success_result(outputs={"skipped": True, "reason": "搜索模式或扫描未完成"})
    if flags.dry_run:
        return success_result(outputs={"skipped": True, "reason": "dry-run：不写正式索引文件（JSON/MD/CSV）"})
    root = ctx.state["root"]
    records = ctx.state["records"]
    summary = ctx.state["summary"]
    indexer.write_json(root, records, summary)
    indexer.write_csv(records)
    indexer.write_md(root, records, summary)
    ctx.state["written"] = True
    return success_result(
        outputs={"json_path": str(indexer.JSON_PATH), "md_path": str(indexer.MD_PATH), "csv_path": str(indexer.CSV_PATH)}
    )


def collect_artifacts(ctx: StepContext):
    flags = ctx.state["flags"]
    # 收尾卸载 NAS（与 legacy main 的 finally 一致）。
    if ctx.state.get("should_mount") and not flags.keep_mounted and not ctx.state.get("mounted_before"):
        try:
            nas.unmount_nas()
        except Exception:  # noqa: BLE001 - 卸载失败不应影响产物汇总
            pass

    artifacts = []
    if ctx.state.get("written"):
        for path in (indexer.JSON_PATH, indexer.MD_PATH, indexer.CSV_PATH):
            if Path(path).exists():
                artifacts.append(Artifact(type=Path(path).suffix.lstrip("."), role="nas_index", name=Path(path).name, path=str(path)))

    if ctx.state["mode"] == "search":
        result = ctx.state.get("search_result") or {}
        return success_result(
            outputs={"task": "company_nas_index", "mode": "search", "match_count": result.get("match_count", 0), "matches": result.get("matches", [])}
        )
    return success_result(
        outputs={
            "task": "company_nas_index",
            "mode": "build",
            "dry_run": flags.dry_run,
            "summary": ctx.state.get("summary"),
            "written": bool(ctx.state.get("written")),
        },
        artifacts=artifacts,
    )
