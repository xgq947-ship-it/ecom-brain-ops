"""天猫商品价格监控 workflow 的 step handler。

链路：商品ID → 猫超商品列表查条码 → 聚水潭商品资料查淘系控价 → 抓天猫实时价 → 对比。

平台抓价经 clients/ops_cli_client.py -> Ops-Cli（ops tmall price get），本层只做
控价匹配（纯本地 Excel）与对比产出，不写天猫 URL / Cookie / Selector / Playwright / CDP。

dry-run 安全点：
- 控价匹配只读本地 Excel，dry-run/真实都安全。
- fetch 步骤向 Ops-Cli 透传 --dry-run，平台层返回模拟价格，不访问天猫。
- 登录失效只记录到 workflow outputs，不发送任何通知渠道。
- 产出为 outputs/tmall_price_monitor 下的一次性报表（非主数据），dry-run 仍写出便于自测。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from clients.ops_cli_client import run_ops_json
from core.runtime import Artifact, StepContext, failure_result, parse_workflow_args, success_result

from workflows.tmall_price_monitor import report_writer
from workflows.tmall_price_monitor.control_price_mapper import (
    STATUS_MATCHED,
    ControlPriceResolver,
    MappingError,
    clean_item_id,
)
from workflows.tmall_price_monitor.price_compare import (
    InputError,
    build_record,
    resolve_item_ids,
    summarize,
)


BUSINESS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = BUSINESS_ROOT / "outputs" / "tmall_price_monitor"
AUTH_FAILURE_STATUSES = {"login_required", "captcha"}


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--item-id", default=None)
    parser.add_argument("--item-ids", default=None)
    parser.add_argument("--file", default=None)
    parser.add_argument("--maochao-file", default=None)
    parser.add_argument("--jst-file", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def _split_refs(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.replace("，", ",").split(",") if piece.strip()]


def _source_url_by_item_id(flags: argparse.Namespace, item_ids: list[str]) -> dict[str, str]:
    sources: dict[str, str] = {}
    known = set(item_ids)
    for raw in [*(_split_refs(flags.item_ids)), *(_split_refs(flags.item_id))]:
        if not raw.lower().startswith(("http://", "https://")):
            continue
        item_id = clean_item_id(raw)
        if item_id in known:
            sources[item_id] = raw
    return sources


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)

    if flags.file:
        file_path = Path(flags.file).expanduser()
        if not file_path.exists():
            return failure_result(errors=[f"输入文件不存在：{file_path}"])

    try:
        item_ids = resolve_item_ids(item_id=flags.item_id, item_ids=flags.item_ids, file=flags.file)
    except InputError as exc:
        return failure_result(errors=[str(exc)])

    output_dir = Path(flags.output_dir).expanduser() if flags.output_dir else DEFAULT_OUTPUT_DIR
    ctx.state["flags"] = flags
    ctx.state["item_ids"] = item_ids
    ctx.state["source_urls"] = _source_url_by_item_id(flags, item_ids)
    ctx.state["output_dir"] = output_dir
    return success_result(
        outputs={"dry_run": flags.dry_run, "item_count": len(item_ids), "item_ids": item_ids, "output_dir": str(output_dir)}
    )


def resolve_control_prices(ctx: StepContext):
    flags = ctx.state["flags"]
    item_ids = ctx.state["item_ids"]

    # 读两张本地表（猫超商品列表 + 聚水潭商品资料）。文件/字段缺失给明确中文原因。
    try:
        resolver = ControlPriceResolver(maochao_path=flags.maochao_file, jst_path=flags.jst_file)
    except MappingError as exc:
        return failure_result(errors=[str(exc)])
    except Exception as exc:  # noqa: BLE001 - 兜底成可读错误，不抛裸 traceback
        return failure_result(errors=[f"读取控价文件失败：{exc}"])

    mappings: dict[str, dict] = {}
    for item_id in item_ids:
        # 单个商品匹配失败不影响其它（resolve 内部已不抛异常，这里再兜一层）。
        try:
            mappings[item_id] = resolver.resolve(item_id)
        except Exception as exc:  # noqa: BLE001
            mappings[item_id] = {
                "item_id": item_id,
                "mapping_status": "未找到猫超条码",
                "taoxi_control_price": None,
                "barcode": "",
                "jst_goods_code": "",
                "jst_goods_name": "",
                "maochao_name": "",
                "matched_barcode_count": 0,
                "all_control_prices": [],
                "error": f"控价匹配异常：{exc}",
            }

    matched_ids = [iid for iid, m in mappings.items() if m.get("mapping_status") == STATUS_MATCHED]
    ctx.state["mappings"] = mappings
    ctx.state["matched_ids"] = matched_ids
    return success_result(
        outputs={
            "maochao_file": str(resolver.maochao_path),
            "jst_file": str(resolver.jst_path),
            "matched_count": len(matched_ids),
            "mapping_status": {iid: m.get("mapping_status") for iid, m in mappings.items()},
            "control_prices": {iid: m.get("taoxi_control_price") for iid, m in mappings.items()},
        }
    )


def fetch_realtime_prices(ctx: StepContext):
    flags = ctx.state["flags"]
    matched_ids = ctx.state["matched_ids"]
    output_dir = ctx.state["output_dir"]

    # 只对匹配到控价的商品抓实时价，避免对无控价商品做无谓的天猫请求。
    if not matched_ids:
        ctx.state["rows"] = []
        ctx.state["source"] = "simulated" if flags.dry_run else "page"
        return success_result(outputs={"fetched_rows": 0, "skipped": "无匹配到控价的商品，跳过抓价"})

    screenshot_dir = output_dir / "screenshots"
    source_urls: dict[str, str] = ctx.state.get("source_urls") or {}
    item_refs = [source_urls.get(item_id, item_id) for item_id in matched_ids]
    command = ["--json", "tmall", "price", "get", "--item-ids", ",".join(item_refs), "--screenshot-dir", str(screenshot_dir)]
    if flags.dry_run:
        command.append("--dry-run")

    try:
        payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        return failure_result(errors=[f"Ops-Cli 调用失败：{exc}"])

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return failure_result(errors=[f"Ops-Cli 返回缺少 rows 字段：{data}"])

    ctx.state["rows"] = data["rows"]
    ctx.state["source"] = str(data.get("source") or ("simulated" if flags.dry_run else "page"))
    return success_result(outputs={"fetched_rows": len(data["rows"]), "source": ctx.state["source"]})


def compare_prices(ctx: StepContext):
    item_ids = ctx.state["item_ids"]
    mappings = ctx.state["mappings"]
    rows = ctx.state["rows"]
    row_by_id: dict[str, dict] = {}
    for row in rows:
        row_by_id.setdefault(str(row.get("item_id") or ""), row)

    records = [build_record(row_by_id.get(iid), mappings[iid]) for iid in item_ids]
    ctx.state["records"] = records

    summary = summarize(records)
    below = [r for r in records if r["status"] == "低于控价"]
    return success_result(
        outputs={
            "total": len(records),
            "summary": summary,
            "below_control_count": len(below),
            "below_control": [
                {
                    "item_id": r["item_id"],
                    "title": r["title"],
                    "barcode": r["barcode"],
                    "jst_goods_code": r["jst_goods_code"],
                    "taoxi_control_price": r["taoxi_control_price"],
                    "realtime_price": r["realtime_price"],
                    "diff_price": r["diff_price"],
                }
                for r in below
            ],
        }
    )


def _collect_auth_failures(records: list[dict]) -> list[dict]:
    affected = [record for record in records if record.get("capture_status") in AUTH_FAILURE_STATUSES]
    return [
        {
            "item_id": record.get("item_id"),
            "capture_status": record.get("capture_status"),
            "status": "登录失效" if record.get("capture_status") == "login_required" else "滑块/安全验证",
            "title": record.get("title") or record.get("jst_goods_name") or "",
        }
        for record in affected
    ]


def notify_login_if_needed(ctx: StepContext):
    records = ctx.state.get("records") or []
    affected = _collect_auth_failures(records)
    if not affected:
        notification = {"sent": False, "reason": "无登录/验证码异常，默认不发送通知"}
        ctx.state["login_notification"] = notification
        return success_result(outputs={"affected_count": 0, "notification": notification})

    notification = {"sent": False, "reason": "登录失效通知已按当前策略关闭"}
    ctx.state["login_notification"] = notification
    return success_result(
        outputs={
            "affected_count": len(affected),
            "affected_item_ids": [record.get("item_id") for record in affected],
            "affected_items": affected,
            "notification": notification,
        }
    )


def write_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    records = ctx.state["records"]
    output_dir = ctx.state["output_dir"]
    source = ctx.state.get("source", "page")

    ts = report_writer.timestamp()
    xlsx_name, json_name = report_writer.output_names(ts)
    xlsx_path = output_dir / xlsx_name
    json_path = output_dir / json_name

    payload = report_writer.build_json_payload(records, dry_run=flags.dry_run, source=source)
    written_xlsx = report_writer.write_excel(xlsx_path, records)
    written_json = report_writer.write_json(json_path, payload)

    artifacts = [
        Artifact(type="xlsx", role="output", name=written_xlsx.name, path=str(written_xlsx), platform="tmall", metadata={"total": len(records), "below_control_count": payload["below_control_count"]}),
        Artifact(type="json", role="output", name=written_json.name, path=str(written_json), platform="tmall", metadata={"total": len(records), "below_control_count": payload["below_control_count"]}),
    ]
    for art in artifacts:
        ctx.add_artifact(art)

    ctx.state["payload"] = payload
    return success_result(outputs={"xlsx_path": str(written_xlsx), "json_path": str(written_json)}, artifacts=artifacts)


def collect_outputs(ctx: StepContext):
    payload = dict(ctx.state["payload"])
    payload["login_notification"] = ctx.state.get("login_notification")
    return success_result(outputs=payload)
