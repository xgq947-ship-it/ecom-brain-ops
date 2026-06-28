from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ops_cli.config import get_config
from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.output import CommandResponse
from ops_cli.platforms.jst import order as jst_order
from ops_cli.utils.http import build_client


CONFIG_PATH = Path("data/jst/pickup_watch_config.json")


def _load_keywords() -> list[str]:
    env_value = os.getenv("JST_PICKUP_WATCH_KEYWORDS", "").strip()
    if env_value:
        return [item.strip() for item in env_value.split(",") if item.strip()]
    path = Path.cwd() / CONFIG_PATH
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("pickup_keywords") or []
        if isinstance(values, list):
            return [str(item).strip() for item in values if str(item).strip()]
    raise RuntimeError(f"揽收关键词配置缺失：{path}")


def _trace_text(event: Any) -> str:
    if isinstance(event, dict):
        return " ".join(str(value) for value in event.values() if value is not None)
    return str(event or "")


def detect_pickup_record(traces: list[Any], keywords: list[str] | None = None) -> tuple[bool, str]:
    matched_keywords = keywords if keywords is not None else _load_keywords()
    trace_text = "\n".join(_trace_text(event) for event in traces)
    for keyword in matched_keywords:
        if keyword and keyword in trace_text:
            return True, keyword
    return False, ""


def _order(
    *,
    now: datetime,
    suffix: str,
    hours_ago: float,
    platform: str = "天猫超市",
    logistics_no: str = "",
    traces: list[dict[str, str]] | None = None,
    status: str = "",
) -> dict[str, Any]:
    trace_items = traces or []
    picked_up, keyword = detect_pickup_record(trace_items)
    pay_time = now - timedelta(hours=hours_ago)
    return {
        "shop_name": "dry-run 猫超店铺" if "猫超" in platform else "dry-run 其他店铺",
        "platform_order_no": f"DRY-PLATFORM-{suffix}",
        "jst_order_no": f"DRY-JST-{suffix}",
        "platform": platform,
        "order_source": platform,
        "jst_pay_time": pay_time.isoformat(timespec="seconds"),
        "maochao_real_pay_time": "",
        "logistics_company": "顺丰速运",
        "logistics_no": logistics_no,
        "latest_logistics_status": status,
        "logistics_traces": trace_items,
        "has_pickup_record": picked_up,
        "pickup_matched_keyword": keyword,
        "raw": {"dry_run_case": suffix},
    }


def _dry_run_orders(now: datetime) -> list[dict[str, Any]]:
    today_after_stop = now.replace(hour=17, minute=45, second=0, microsecond=0)
    rows = [
        _order(
            now=now,
            suffix="PICKED",
            hours_ago=26,
            logistics_no="SFDRY001",
            status="运输中",
            traces=[{"time": now.isoformat(timespec="seconds"), "content": "快件已由顺丰收取"}],
        ),
        _order(now=now, suffix="NORMAL", hours_ago=6),
        _order(now=now, suffix="REMIND", hours_ago=13, logistics_no="SFDRY003"),
        _order(now=now, suffix="HIGH", hours_ago=21, logistics_no="SFDRY004"),
        _order(now=now, suffix="TIMEOUT", hours_ago=25),
        _order(now=now, suffix="MAOCHAO-OFFSET", hours_ago=19.75),
        _order(now=now, suffix="NO-TRACE", hours_ago=14, logistics_no="SFDRY007"),
        _order(
            now=now,
            suffix="TRACE-NO-PICKUP",
            hours_ago=22,
            logistics_no="SFDRY008",
            status="已发货",
            traces=[{"time": now.isoformat(timespec="seconds"), "content": "运单已创建，待快递收件"}],
        ),
    ]
    rows.append(
        {
            **_order(now=now, suffix="AFTER-1730", hours_ago=0.0, platform="其他平台"),
            "jst_pay_time": today_after_stop.isoformat(timespec="seconds"),
        }
    )
    return rows


def _parse_pay_time(value: Any, now: datetime) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=now.tzinfo)
        except ValueError:
            continue
    return None


def _query_page_rows(
    client: Any,
    *,
    url: str,
    cookie: str,
    form_template: dict[str, str],
    page: int,
) -> list[dict[str, Any]]:
    payload = jst_order._request_jst(
        client,
        url,
        cookie,
        "LoadDataToJSON",
        {
            "Method": "LoadDataToJSON",
            "Args": [str(page), "[]", "{}"],
        },
        form_template=form_template,
    )
    if isinstance(payload, dict) and isinstance(payload.get("ReturnValue"), str):
        try:
            payload = json.loads(payload["ReturnValue"])
        except json.JSONDecodeError:
            pass
    return jst_order._iter_rows(payload)


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _summarize_traces(traces: list[dict[str, Any]]) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    for trace in traces:
        status = _first_text(trace, ("StatusSrc", "status", "sub_status"))
        timestamp = _first_text(trace, ("created", "time", "db_created"))
        summary = {"status": status, "time": timestamp}
        if status or timestamp:
            summaries.append(summary)
    return summaries


def _fetch_paid_orders(
    *,
    hours: int,
    shop_name: str | None,
    debug: bool,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    del debug
    checked_at = now or datetime.now().astimezone()
    cutoff = checked_at - timedelta(hours=hours)
    session = get_scene_manager().ensure_scene(jst_order.JST_SITE, jst_order.JST_ORDER_SCENE)
    headers = dict(session.get("headers") or {})
    cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
    if not cookie:
        raise RuntimeError("SessionHub 已返回 order_list session，但缺少 Cookie。请重新捕获聚水潭会话。")
    url = str(session.get("url") or f"https://www.erp321.com{jst_order.DEFAULT_JST_ORDER_PATH}").strip()
    form_template = jst_order._extract_form_template(session)
    max_pages = int(os.getenv("JST_PICKUP_WATCH_MAX_PAGES", "100"))
    orders: list[dict[str, Any]] = []
    with build_client(follow_redirects=True, timeout=60.0) as client:
        for page in range(1, max_pages + 1):
            rows = _query_page_rows(client, url=url, cookie=cookie, form_template=form_template, page=page)
            if not rows:
                break
            oldest_pay_time: datetime | None = None
            for row in rows:
                pay_time = _parse_pay_time(row.get("pay_date") or row.get("pay_time"), checked_at)
                if pay_time is not None and (oldest_pay_time is None or pay_time < oldest_pay_time):
                    oldest_pay_time = pay_time
                if pay_time is None or pay_time < cutoff or not bool(row.get("is_paid")):
                    continue
                current_shop = _first_text(row, ("shop_name", "store_name", "shop"))
                if shop_name and current_shop != shop_name:
                    continue
                logistics_no = _first_text(row, jst_order.LOGISTICS_NUMBER_KEYS)
                if logistics_no:
                    logistics = jst_order.resolve_logistics_from_row(client, session, row)
                    logistics_no = str(logistics["logistics_no"])
                    company = str(logistics["logistics_company"])
                    status = str(logistics["logistics_status"])
                    traces = list(logistics["trace_events"])
                else:
                    company = _first_text(row, jst_order.LOGISTICS_COMPANY_KEYS)
                    status = _first_text(row, jst_order.LOGISTICS_STATUS_KEYS)
                    traces = []
                has_pickup_record, matched_keyword = detect_pickup_record(traces)
                trace_summaries = _summarize_traces(traces)
                orders.append(
                    {
                        "shop_name": current_shop,
                        "platform_order_no": _first_text(row, ("outer_so_id", "raw_so_id", "so_id")),
                        "jst_order_no": _first_text(row, ("o_id", "so_id")),
                        "platform": _first_text(row, ("order_from", "plat_channel")),
                        "order_source": _first_text(row, ("order_from", "plat_channel")),
                        "jst_pay_time": pay_time.isoformat(timespec="seconds"),
                        "maochao_real_pay_time": "",
                        "logistics_company": company,
                        "logistics_no": logistics_no,
                        "latest_logistics_status": status,
                        "logistics_traces": trace_summaries,
                        "has_pickup_record": has_pickup_record,
                        "pickup_matched_keyword": matched_keyword,
                        "raw": {
                            "status": row.get("status"),
                            "order_from": row.get("order_from"),
                            "is_paid": row.get("is_paid"),
                        },
                    }
                )
            if oldest_pay_time is not None and oldest_pay_time < cutoff:
                break
    return orders


def run_pickup_watch(
    *,
    hours: int = 48,
    shop_name: str | None = None,
    dry_run: bool = False,
    debug: bool = False,
) -> CommandResponse:
    if hours <= 0:
        raise RuntimeError("--hours 必须大于 0")
    now = datetime.now().astimezone()
    selected_shop = shop_name or ("" if dry_run else get_config().jst_order_stats_store)
    orders = _dry_run_orders(now) if dry_run else _fetch_paid_orders(hours=hours, shop_name=selected_shop, debug=debug)
    return CommandResponse(
        success=True,
        platform="jst",
        command="order pickup-watch",
        data={
            "hours": hours,
            "shop_name": selected_shop,
            "checked_at": now.isoformat(timespec="seconds"),
            "dry_run": dry_run,
            "debug": debug,
            "orders": orders,
            "artifacts": [],
        },
    )
