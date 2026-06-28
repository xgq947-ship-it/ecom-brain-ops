"""猫超价格竞争力商品查询 workflow 的 step handler（快照 + 缓存 + 批量匹配）。

提速思路：整张「每日跟价商品」列表不大，一次性抓全缓存到本地 JSON（按天），
之后单个 / 批量商品编码都直接在缓存里精确匹配，秒出结果；仅缓存缺失 / 跨天 /
--refresh 时才再跑一次浏览器（调用 Ops-Cli `tmcs price-competitiveness list`）。

业务层只通过 clients.ops_cli_client.run_ops_json 调用 Ops-Cli，
不写猫超 URL、Cookie、Token、Selector、Playwright、CDP。

dry-run 安全点：
- load 步骤向 Ops-Cli 透传 --dry-run，平台层返回 simulated 空列表、不访问真实猫超；
  且 dry-run 不写缓存文件。
- 本 workflow 为只读查询，不发通知、不写主数据、不下载文件，无其它危险动作。
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any

from clients.ops_cli_client import run_ops_json
from core.runtime import StepContext, failure_result, parse_workflow_args, success_result

from workflows.tmcs_price_competitiveness_lookup import cache


PRODUCT_CODE_REQUIRED = "PRODUCT_CODE_REQUIRED"


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--product-code", default=None)
    parser.add_argument("--product-codes", default=None)
    parser.add_argument("--codes-file", default=None)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--screenshot-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    try:
        codes = cache.parse_codes(
            product_code=flags.product_code,
            product_codes=flags.product_codes,
            codes_file=flags.codes_file,
        )
    except FileNotFoundError as exc:
        return failure_result(errors=[str(exc)])

    if not codes:
        return failure_result(
            errors=[
                f"{PRODUCT_CODE_REQUIRED}：缺少商品编码，请用 --product-code / "
                "--product-codes a,b,c / --codes-file <文件> 至少提供一个。"
            ],
            outputs={"error_code": PRODUCT_CODE_REQUIRED},
        )

    ctx.state["flags"] = flags
    ctx.state["codes"] = codes
    return success_result(
        outputs={
            "codes": codes,
            "code_count": len(codes),
            "refresh": flags.refresh,
            "dry_run": flags.dry_run,
        }
    )


def _fetch_list_via_ops(flags) -> dict[str, Any]:
    command = ["--json", "tmcs", "price-competitiveness", "list"]
    if flags.screenshot_dir:
        command += ["--screenshot-dir", flags.screenshot_dir]
    if flags.dry_run:
        command.append("--dry-run")
    payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    return payload.get("data") or {}


def load_list(ctx: StepContext):
    """加载整张列表：优先当天缓存；缺失 / 跨天 / --refresh / dry-run 时调用 Ops-Cli 重抓。"""
    flags = ctx.state["flags"]
    today = date.today().isoformat()

    if not flags.refresh and not flags.dry_run:
        cached = cache.load_cache(today)
        if cached is not None:
            ctx.state["rows"] = cached.get("rows") or []
            ctx.state["list_meta"] = {
                "from_cache": True,
                "list_date": cached.get("list_date") or today,
                "captured_at": cached.get("captured_at"),
                "total_rows": cached.get("total_rows"),
                "cache_path": str(cache.cache_path(today)),
                "source": "cache",
            }
            return success_result(outputs=ctx.state["list_meta"] | {"row_count": len(ctx.state["rows"])})

    try:
        data = _fetch_list_via_ops(flags)
    except RuntimeError as exc:
        return failure_result(errors=[f"Ops-Cli 调用失败：{exc}"])

    rows = data.get("rows")
    if not isinstance(rows, list):
        return failure_result(errors=[f"Ops-Cli 返回缺少 rows 字段：{data}"])

    list_date = data.get("list_date") or today
    simulated = bool(data.get("simulated", False))
    cache_path = None
    # dry-run / 模拟数据不写缓存，避免污染真实当天缓存。
    if not flags.dry_run and not simulated:
        cache_path = str(
            cache.save_cache(
                list_date,
                {
                    "captured_at": data.get("captured_at"),
                    "total_rows": data.get("total_rows"),
                    "rows": rows,
                },
            )
        )

    ctx.state["rows"] = rows
    ctx.state["list_meta"] = {
        "from_cache": False,
        "list_date": list_date,
        "captured_at": data.get("captured_at"),
        "total_rows": data.get("total_rows"),
        "cache_path": cache_path,
        "source": data.get("source"),
        "simulated": simulated,
        "ops_context_path": data.get("context_path"),
    }
    return success_result(outputs=ctx.state["list_meta"] | {"row_count": len(rows)})


def match_codes(ctx: StepContext):
    codes: list[str] = ctx.state["codes"]
    rows: list[dict[str, Any]] = ctx.state.get("rows") or []
    results = cache.match_codes(codes, rows)
    found = [r["product_code"] for r in results if r["exists"]]
    missing = [r["product_code"] for r in results if not r["exists"]]
    ctx.state["results"] = results
    ctx.state["found"] = found
    ctx.state["missing"] = missing
    return success_result(
        outputs={
            "results": results,
            "found_count": len(found),
            "missing_count": len(missing),
            "found": found,
            "missing": missing,
        }
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    meta = ctx.state.get("list_meta") or {}
    results = ctx.state.get("results") or []
    found = ctx.state.get("found") or []
    missing = ctx.state.get("missing") or []

    if len(results) == 1:
        verdict = "存在" if results[0]["exists"] else "不存在"
        message = (
            f"商品编码 {results[0]['product_code']} 在价格竞争力列表中【{verdict}】"
            f"（命中 {len(results[0]['matched_items'])} 条）。"
        )
    else:
        message = (
            f"共查询 {len(results)} 个商品编码：存在 {len(found)} 个、不存在 {len(missing)} 个。"
        )

    return success_result(
        outputs={
            "task": "tmcs_price_competitiveness_lookup",
            "dry_run": flags.dry_run,
            "message": message,
            "results": results,
            "found": found,
            "missing": missing,
            "found_count": len(found),
            "missing_count": len(missing),
            "from_cache": meta.get("from_cache"),
            "list_date": meta.get("list_date"),
            "captured_at": meta.get("captured_at"),
            "total_rows": meta.get("total_rows"),
            "cache_path": meta.get("cache_path"),
            "source": meta.get("source"),
            "simulated": bool(meta.get("simulated", False)),
        }
    )
