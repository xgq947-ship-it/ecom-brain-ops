from __future__ import annotations

import argparse
from pathlib import Path

from core.config_loader import get_path
from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.tmcs_sku_roi.excel_lookup import (
    find_jst_product,
    find_tmcs_barcode,
    load_roi_config,
    write_result_excel,
    write_result_json,
)
from workflows.tmcs_sku_roi.roi_calculator import calculate_roi as calculate_roi_value


DEFAULT_TMCS_FILE = get_path("tmall_goods_master_file")
DEFAULT_JST_FILE = get_path("jst_product_master_file")
DEFAULT_ROI_CONFIG_FILE = get_path("project_root") / "config" / "tmcs_sku_roi.json"


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--sku-code", required=False)
    parser.add_argument("--product-code", required=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--tmcs-file", default=str(DEFAULT_TMCS_FILE))
    parser.add_argument("--jst-file", default=str(DEFAULT_JST_FILE))
    parser.add_argument("--roi-config", default=str(DEFAULT_ROI_CONFIG_FILE))
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def _format_roi(value) -> str:
    if value is None:
        return "无利润"
    return f"{value:.4f}"


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    if bool(flags.sku_code) == bool(flags.product_code):
        return failure_result("必须且只能提供一个查询参数：--sku-code 或 --product-code")

    tmcs_file = Path(flags.tmcs_file).expanduser()
    jst_file = Path(flags.jst_file).expanduser()
    roi_config_file = Path(flags.roi_config).expanduser()
    missing = [str(path) for path in (tmcs_file, jst_file, roi_config_file) if not path.exists()]
    if missing:
        return failure_result([f"文件不存在：{path}" for path in missing])

    try:
        config = load_roi_config(roi_config_file)
    except ValueError as exc:
        return failure_result(str(exc))

    ctx.state["flags"] = flags
    ctx.state["tmcs_file"] = tmcs_file
    ctx.state["jst_file"] = jst_file
    ctx.state["roi_config_file"] = roi_config_file
    ctx.state["roi_config"] = config
    return success_result()


def lookup_tmcs_barcode(ctx: StepContext):
    flags = ctx.state["flags"]
    result = find_tmcs_barcode(
        ctx.state["tmcs_file"],
        sku_code=flags.sku_code,
        product_code=flags.product_code,
    )
    ctx.state["tmcs_result"] = result
    return success_result()


def lookup_jst_product(ctx: StepContext):
    barcode = ctx.state["tmcs_result"]["barcode"]
    result = find_jst_product(ctx.state["jst_file"], barcode)
    ctx.state["jst_result"] = result
    return success_result()


def calculate_roi(ctx: StepContext):
    jst_result = ctx.state["jst_result"]
    roi_result = calculate_roi_value(jst_result["price"], jst_result["cost"], config=ctx.state["roi_config"])
    ctx.state["roi_result"] = roi_result
    return success_result()


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    roi_result = ctx.state["roi_result"]
    payload = {
        "保本ROI": _format_roi(roi_result["break_even_roi"]),
        "安全ROI": _format_roi(roi_result["safe_roi"]),
        "理想ROI": _format_roi(roi_result["ideal_roi"]),
    }
    if flags.dry_run:
        return success_result(outputs=payload)

    output = flags.output
    artifacts = []
    if output:
        output_path = Path(output).expanduser()
        suffix = output_path.suffix.lower()
        if suffix == ".json":
            written_path = write_result_json(output_path, payload)
            art_type = "json"
        elif suffix == ".xlsx":
            written_path = write_result_excel(output_path, payload)
            art_type = "xlsx"
        else:
            return failure_result(f"--output 仅支持 .json 或 .xlsx，当前为：{output_path.suffix or '无后缀'}")
        artifacts.append(
            Artifact(type=art_type, role="output", name=written_path.name, path=str(written_path), platform="tmcs")
        )
    return success_result(outputs=payload, artifacts=artifacts)
