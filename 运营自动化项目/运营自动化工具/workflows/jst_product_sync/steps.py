"""更新聚水潭商品资料 workflow 的 step handler。

平台调用经 clients/ops_cli_client.py -> Ops-Cli（ops jst product sync），本层只做编排，
不重写同步逻辑。聚水潭资料同步在 Ops-Cli 内部一次完成下载/校验/写主表。

dry-run 安全点：向 ops 透传 --dry-run，Ops-Cli 只预览、不覆盖主数据；interactive_recovery
由公共客户端按 --dry-run 自动判定为 False，不拉起浏览器。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result
from core.config_loader import get_path
from clients.ops_cli_client import run_ops_json

DEFAULT_KEEP_BRANDS = ("奥克斯", "苏泊尔")
DEFAULT_SOURCE = get_path("jst_product_import_file")


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--use-local-only", action="store_true")
    parser.add_argument("--keep-brands", nargs="+", default=list(DEFAULT_KEEP_BRANDS))
    parser.add_argument("--no-filter", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def _data(ctx: StepContext) -> dict:
    payload = ctx.state.get("payload") or {}
    data = payload.get("data") if isinstance(payload, dict) else {}
    return data if isinstance(data, dict) else {}


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "use_local_only": flags.use_local_only,
            "keep_brands": [] if flags.no_filter else list(flags.keep_brands),
        }
    )


def check_local_source(ctx: StepContext):
    source = Path(str(DEFAULT_SOURCE)).expanduser()
    ctx.state["source"] = source
    return success_result(outputs={"source": str(source), "source_exists": source.exists()})


def download_jst_products(ctx: StepContext):
    flags = ctx.state["flags"]
    command = ["--json", "jst", "product", "sync"]
    if flags.dry_run:
        command.append("--dry-run")
    if flags.use_local_only:
        command.append("--use-local-only")
    if not flags.no_filter and flags.keep_brands:
        command.extend(["--keep-brands", flags.keep_brands[0], *flags.keep_brands[1:]])
    try:
        payload = run_ops_json(command)
    except Exception as exc:  # noqa: BLE001 - dry-run 容忍平台/数据未就绪，降级为安全预览
        if flags.dry_run:
            ctx.state["no_sync"] = True
            return success_result(outputs={"skipped": True, "reason": str(exc)})
        return failure_result(str(exc))
    ctx.state["payload"] = payload
    data = payload.get("data") if isinstance(payload, dict) else {}
    data = data if isinstance(data, dict) else {}
    return success_result(
        outputs={
            "success": bool(payload.get("success")) if isinstance(payload, dict) else False,
            "command": payload.get("command") if isinstance(payload, dict) else None,
            "import_file": data.get("import_file"),
        }
    )


def validate_products(ctx: StepContext):
    data = _data(ctx)
    return success_result(outputs={"targets": data.get("targets")})


def update_master_data(ctx: StepContext):
    flags = ctx.state["flags"]
    data = _data(ctx)
    if flags.dry_run:
        return success_result(
            outputs={"skipped": True, "reason": "dry-run：Ops-Cli 仅预览，未覆盖主数据", "latest_file": data.get("latest_file")}
        )
    return success_result(outputs={"latest_file": data.get("latest_file"), "written": True})


def collect_artifacts(ctx: StepContext):
    flags = ctx.state["flags"]
    data = _data(ctx)
    artifacts = []
    latest_file = data.get("latest_file")
    if not flags.dry_run and latest_file and Path(str(latest_file)).exists():
        artifacts.append(
            Artifact(type="xlsx", role="master_latest", name=Path(str(latest_file)).name, path=str(latest_file), platform="jst")
        )
    return success_result(
        outputs={
            "task": "update_jst_products",
            "dry_run": flags.dry_run,
            "import_file": data.get("import_file"),
            "latest_file": latest_file,
            "targets": data.get("targets"),
        },
        artifacts=artifacts,
    )
