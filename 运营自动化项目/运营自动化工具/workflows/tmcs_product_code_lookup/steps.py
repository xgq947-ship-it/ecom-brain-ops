from __future__ import annotations

import argparse
from pathlib import Path

from core.config_loader import get_path
from core.runtime import Artifact, StepContext, failure_result, parse_workflow_args, success_result

from workflows.tmcs_product_code_lookup.excel_lookup import (
    FieldResolutionError,
    fuzzy_match_products,
    load_online_products,
    write_result_excel,
    write_result_json,
)


DEFAULT_SOURCE_FILE = get_path("maochao_goods_master_file")


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model", default=None)
    parser.add_argument("--brand", default=None)
    parser.add_argument("--source-file", default=str(DEFAULT_SOURCE_FILE))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-score", type=float, default=0.5)
    parser.add_argument("--output", default=None)
    # 默认按猫超商品编码去重（同一编码只出一条）；--by-sku 退回 SKU 粒度明细。
    parser.add_argument("--by-sku", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)

    model = (flags.model or "").strip()
    if not model:
        return failure_result("缺少必填参数 --model（商品型号或型号关键词）")

    source_file = Path(flags.source_file).expanduser()
    if not source_file.exists():
        return failure_result(f"猫超商品列表文件不存在：{source_file}")

    if flags.limit <= 0:
        return failure_result(f"--limit 必须为正整数，当前为：{flags.limit}")
    if not 0.0 <= flags.min_score <= 1.0:
        return failure_result(f"--min-score 必须在 0~1 之间，当前为：{flags.min_score}")

    flags.model = model
    flags.brand = (flags.brand or "").strip() or None
    ctx.state["flags"] = flags
    ctx.state["source_file"] = source_file
    return success_result(
        outputs={
            "model": model,
            "brand": flags.brand,
            "source_file": str(source_file),
            "limit": flags.limit,
            "min_score": flags.min_score,
        }
    )


def load_tmcs_products(ctx: StepContext):
    source_file = ctx.state["source_file"]
    # 只读 Excel，不修改原文件；dry-run 下也安全（无平台请求、无写入）。
    try:
        products, resolved = load_online_products(source_file)
    except FieldResolutionError as exc:
        return failure_result(str(exc))
    ctx.state["products"] = products
    return success_result(
        outputs={
            "online_count": len(products),
            "resolved_fields": sorted(resolved.keys()),
        }
    )


def fuzzy_match_step(ctx: StepContext):
    flags = ctx.state["flags"]
    results = fuzzy_match_products(
        ctx.state["products"],
        model=flags.model,
        brand=flags.brand,
        min_score=flags.min_score,
        limit=flags.limit,
        dedupe_by="sku" if flags.by_sku else "product_code",
    )
    ctx.state["results"] = results
    return success_result(outputs={"matched_count": len(results)})


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    results = ctx.state["results"]
    payload = {
        "query": {"brand": flags.brand, "model": flags.model},
        "dedupe_by": "sku" if flags.by_sku else "product_code",
        "matched_count": len(results),
        "results": results,
    }
    if not results:
        payload["message"] = "未找到匹配商品，可放宽 --min-score 或调整 --model/--brand 关键词"

    artifacts: list[Artifact] = []
    output = flags.output
    if output and not flags.dry_run:
        output_path = Path(output).expanduser()
        suffix = output_path.suffix.lower()
        if suffix == ".json":
            written = write_result_json(output_path, payload)
            art_type = "json"
        elif suffix == ".xlsx":
            written = write_result_excel(output_path, payload)
            art_type = "xlsx"
        else:
            return failure_result(
                f"--output 仅支持 .json 或 .xlsx，当前为：{output_path.suffix or '无后缀'}"
            )
        artifacts.append(
            Artifact(
                type=art_type,
                role="output",
                name=written.name,
                path=str(written),
                platform="tmcs",
                metadata={"matched_count": len(results)},
            )
        )
    elif output and flags.dry_run:
        payload["output_skipped"] = f"dry-run 跳过写出文件：{output}"

    return success_result(outputs=payload, artifacts=artifacts)
