import csv
import json
import html
import os
import re
import sys
import time
from datetime import date, datetime
from json import JSONDecoder
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

import httpx

from ops_cli.browser import managed_work_page
from ops_cli.capabilities import mark_scene_refreshed
from ops_cli.capabilities import require_interactive_recovery
from ops_cli.config import get_config
from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.output import CommandResponse
from ops_cli.platforms.auth_shared import is_probable_auth_error
from ops_cli.platforms.jst.shared import surface_jst_login_if_needed
from ops_cli.runtime_context import write_runtime_context
from ops_cli.utils.http import build_client


DEFAULT_INPUT_PATH = Path("data/jst/latest_brush_orders.json")
DEFAULT_JST_ORDER_PATH = "/app/order/order/list.aspx"
JST_SITE = "jst_erp"
JST_ORDER_SCENE = "order_list"
JST_ORDER_LOGISTICS_SCENE = "order_logistics_trace"
ORDER_LOGISTICS_TEMPLATE_PATH = Path("data/jst/order_logistics_template.json")
DEFAULT_REMARK_TEXT = "sfeizao"
DEFAULT_LABELS = "黄色标,特殊单"
CALLBACK_ID = "JTable1"
REMARK_TYPE = "1"
LABEL_REMARK_TYPE = "2"
DEFAULT_FORM_FIELDS = {
    "_jt_page_count_enabled": "",
    "_jt_page_increament_enabled": "true",
    "_jt_page_increament_page_mode": "",
    "_jt_page_increament_key_value": "",
    "_jt_page_increament_business_values": "",
    "_jt_page_increament_key_name": "o_id",
    "_jt_page_size": "50",
    "fe_node_desc": "",
    "receiver_state": "",
    "receiver_city": "",
    "receiver_district": "",
    "receiver_address": "",
    "receiver_name": "",
    "receiver_phone": "",
    "receiver_mobile": "",
    "check_name": "",
    "check_address": "",
    "fe_remark_type": "single",
    "fe_flag": "",
    "fe_is_append_remark": "",
}
ORDER_ID_FILTER_KEYS = ("o_id", "so_id", "raw_so_id", "pre_so_id")
OUTER_ORDER_FILTER_KEY = "outer_so_id"
LOGISTICS_NUMBER_KEYS = (
    "logistics_no",
    "logisticsNumber",
    "express_no",
    "expressNo",
    "l_id",
    "waybill_no",
    "waybillNo",
    "mail_no",
    "mailNo",
    "lp_id",
)
LOGISTICS_COMPANY_KEYS = (
    "logistics_company",
    "logisticsCompany",
    "express_company",
    "expressCompany",
    "lc_name",
    "company",
)
LOGISTICS_STATUS_KEYS = (
    "logistics_status",
    "logisticsStatus",
    "express_status",
    "expressStatus",
    "shipment_status",
    "shipmentStatus",
    "trace_status",
    "traceStatus",
)
ORDER_ITEM_CODE_KEYS = (
    "product_code",
    "sku_code",
    "sku_no",
    "sku_sn",
    "item_code",
    "item_no",
    "item_sku",
    "sku_id",
    "i_id",
    "bn",
)
ORDER_ITEM_NAME_KEYS = (
    "product_name",
    "sku_name",
    "name",
    "item_name",
    "i_name",
    "skus",
)
ORDER_REMARK_KEYS = (
    "remark",
    "seller_remark",
    "fe_remark",
    "memo",
    "buyer_memo",
    "shop_remark",
)
STATUS_ALIASES = {
    "线上已发货": ("线上已发货", "已发货"),
}
SIGNED_KEYWORDS = ("已签收", "签收", "妥投", "已妥投", "delivered", "signed")


class LogisticsTraceAuthorizationRequired(RuntimeError):
    """Raised when JST requires an SMS check before returning logistics traces."""

ORDER_INPUT_HEADER_CANDIDATES = {
    "order",
    "order_id",
    "outer_order_id",
    "order_no",
    "订单号",
    "订单编号",
    "订单id",
}


def _load_orders_from_input_path(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        orders = payload.get("orders")
        if not isinstance(orders, list):
            raise RuntimeError(f"{path.name} 缺少 orders 数组")
        return [str(order).strip() for order in orders if str(order).strip()]

    if suffix in {".txt", ".text"}:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    if suffix == ".csv":
        rows: list[str] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader):
                first = next((str(cell).strip() for cell in row if str(cell).strip()), "")
                if not first:
                    continue
                if index == 0 and first.lower() in ORDER_INPUT_HEADER_CANDIDATES:
                    continue
                rows.append(first)
        return rows

    raise RuntimeError(f"不支持的输入文件格式：{path.suffix or path.name}。目前支持 JSON/TXT/CSV")


def _normalize_identifier_list(values: str | list[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return [str(value).strip() for value in values if str(value).strip()]


def _normalize_orders(
    *,
    order_ids: list[str] | None,
    input_path: str | None,
    limit: int | None,
) -> tuple[list[str], str | None]:
    normalized = [str(order_id).strip() for order_id in (order_ids or []) if str(order_id).strip()]
    resolved_input: str | None = None

    if input_path:
        path = Path(input_path).expanduser().resolve()
        normalized.extend(_load_orders_from_input_path(path))
        resolved_input = str(path)

    if not normalized:
        default_path = Path.cwd() / DEFAULT_INPUT_PATH
        if default_path.exists():
            normalized.extend(_load_orders_from_input_path(default_path))
            resolved_input = str(default_path.resolve())

    unique_orders: list[str] = []
    seen: set[str] = set()
    for order_id in normalized:
        if order_id and order_id not in seen:
            seen.add(order_id)
            unique_orders.append(order_id)

    if limit is not None:
        if limit <= 0:
            raise RuntimeError("--limit 必须大于 0")
        unique_orders = unique_orders[:limit]

    if not unique_orders:
        raise RuntimeError("未提供订单号，也没有找到默认输入文件 data/jst/latest_brush_orders.json")

    return unique_orders, resolved_input


def _extract_json_payload(text: str) -> Any:
    stripped = text.strip()
    decoder = JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(stripped[index:])
            return payload
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"无法从响应中解析 JSON：{stripped[:300]}")


def _iter_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            rows.extend(_iter_rows(item))
        return rows
    if not isinstance(payload, dict):
        return rows
    if "o_id" in payload:
        rows.append(payload)
    for key in ("rows", "Rows", "data", "Data", "datas", "Datas", "items", "Items", "result", "Result"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
        elif isinstance(value, dict):
            rows.extend(_iter_rows(value))
    return rows


def _ensure_success_response(payload: Any, method: str) -> None:
    if not isinstance(payload, dict):
        return
    for key in ("success", "Success", "isSuccess", "IsSuccess"):
        if key in payload and payload[key] is False:
            raise RuntimeError(f"{method} 失败：{payload}")
    for key in ("error", "Error", "errmsg", "errMsg", "message", "Message"):
        value = payload.get(key)
        if isinstance(value, str):
            text = value.strip()
            lowered = text.lower()
            if text and any(flag in lowered for flag in ("失败", "错误", "error", "exception")):
                raise RuntimeError(f"{method} 失败：{text}")


def _base_order_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _extract_form_template(session: dict[str, Any] | None) -> dict[str, str]:
    if not session:
        return dict(DEFAULT_FORM_FIELDS)
    form = session.get("post_data_form")
    if isinstance(form, dict) and form:
        return {**DEFAULT_FORM_FIELDS, **{str(key): str(value) for key, value in form.items()}}
    raw = str(session.get("post_data") or "")
    parsed = parse_qs(raw, keep_blank_values=True)
    if parsed:
        return {**DEFAULT_FORM_FIELDS, **{key: values[0] if values else "" for key, values in parsed.items()}}
    return dict(DEFAULT_FORM_FIELDS)


def _build_request_form(method: str, callback_param: dict[str, Any], form_template: dict[str, str] | None = None) -> dict[str, str]:
    form = dict(form_template or DEFAULT_FORM_FIELDS)
    form.pop("__CALLBACKPARAM", None)
    form.pop("__CALLBACKID", None)
    form["am___"] = method
    form["__CALLBACKID"] = CALLBACK_ID
    form["__CALLBACKPARAM"] = json.dumps(callback_param, ensure_ascii=False, separators=(",", ":"))
    if method == "LoadDataToJSON":
        form["_jt_page_action"] = "1"
    else:
        form.pop("_jt_page_action", None)
    return form


def _request_jst(
    client: httpx.Client,
    url: str,
    cookie: str,
    method: str,
    callback_param: dict[str, Any],
    *,
    form_template: dict[str, str] | None = None,
) -> Any:
    request_url = _base_order_url(url)
    response = client.post(
        request_url,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": cookie,
            "Origin": "https://www.erp321.com",
            "Referer": request_url,
        },
        data=_build_request_form(method, callback_param, form_template=form_template),
    )
    response.raise_for_status()
    payload = _extract_json_payload(response.text)
    _ensure_success_response(payload, method)
    return payload


def _query_order_o_ids(client: httpx.Client, url: str, cookie: str, order_no: str, form_template: dict[str, str] | None = None) -> list[str]:
    payload = _request_jst(
        client,
        url,
        cookie,
        "LoadDataToJSON",
        {
            "Method": "LoadDataToJSON",
            "Args": [
                "1",
                json.dumps([{"k": "outer_so_id", "v": order_no, "c": "@="}], ensure_ascii=False, separators=(",", ":")),
                "{}",
            ],
        },
        form_template=form_template,
    )
    if isinstance(payload, dict) and isinstance(payload.get("ReturnValue"), str):
        try:
            payload = json.loads(payload["ReturnValue"])
        except json.JSONDecodeError:
            pass
    matched: list[str] = []
    for row in _iter_rows(payload):
        value = row.get("o_id")
        if value is None:
            continue
        text = str(value).strip()
        if text:
            matched.append(text)
    return matched


def _append_remark(
    client: httpx.Client,
    url: str,
    cookie: str,
    o_id: str,
    remark_text: str,
    form_template: dict[str, str] | None = None,
    *,
    remark_type: str = REMARK_TYPE,
) -> None:
    _request_jst(
        client,
        url,
        cookie,
        "SaveAppendRemarks",
        {
            "Method": "SaveAppendRemarks",
            "Args": [remark_type, remark_text, o_id, "false"],
            "CallControl": "{page}",
        },
        form_template=form_template,
    )


def _set_normal(
    client: httpx.Client,
    url: str,
    cookie: str,
    o_id: str,
    form_template: dict[str, str] | None = None,
) -> None:
    """异常转正常单：对应订单列表「转正常单」按钮，方法 UnQuestions(o_id)。

    与「转异常」Questions(原因, "", o_id) 对称；只清除异常标记，不改金额/收货等字段。
    """
    _request_jst(
        client,
        url,
        cookie,
        "UnQuestions",
        {
            "Method": "UnQuestions",
            "Args": [o_id],
            "CallControl": "{page}",
        },
        form_template=form_template,
    )


def _set_labels(client: httpx.Client, url: str, cookie: str, o_id: str, labels: str, form_template: dict[str, str] | None = None) -> None:
    _request_jst(
        client,
        url,
        cookie,
        "SetLabels",
        {
            "Method": "SetLabels",
            "Args": [
                json.dumps(
                    {
                        "filter_type": "checked",
                        "set_type": "add",
                        "labels": labels,
                        "o_ids": o_id,
                        "ret_searchable": True,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            ],
            "CallControl": "{page}",
        },
        form_template=form_template,
    )


def _write_failed_orders(results: list[dict[str, Any]], *, prefix: str = "jst_tag_failed_orders") -> str | None:
    failed = [row for row in results if row["status"] != "success"]
    if not failed:
        return None
    output_dir = Path.cwd() / "data"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{prefix}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    output_path.write_text(
        json.dumps({"failed_orders": failed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(output_path)


def _template_path() -> Path:
    return Path.cwd() / ORDER_LOGISTICS_TEMPLATE_PATH


def _sessionhub_root() -> Path:
    return Path(get_scene_manager().root)


def _scene_store_path(site: str, scene: str) -> Path:
    return _sessionhub_root() / "data" / "sessions" / site / f"{scene}.json"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_cookie_header(headers: dict[str, Any], cookies: list[dict[str, Any]] | None) -> dict[str, str]:
    merged = {str(key): str(value) for key, value in headers.items() if str(key).lower() != "cookie"}
    if cookies:
        cookie_header = "; ".join(
            f"{cookie.get('name')}={cookie.get('value')}"
            for cookie in cookies
            if cookie.get("name")
        )
        if cookie_header:
            merged["cookie"] = cookie_header
    return merged


def _query_order_rows(
    client: httpx.Client,
    url: str,
    cookie: str,
    filter_key: str,
    order_no: str,
    form_template: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    payload = _request_jst(
        client,
        url,
        cookie,
        "LoadDataToJSON",
        {
            "Method": "LoadDataToJSON",
            "Args": [
                "1",
                json.dumps([{"k": filter_key, "v": order_no, "c": "@="}], ensure_ascii=False, separators=(",", ":")),
                "{}",
            ],
        },
        form_template=form_template,
    )
    if isinstance(payload, dict) and isinstance(payload.get("ReturnValue"), str):
        try:
            payload = json.loads(payload["ReturnValue"])
        except json.JSONDecodeError:
            pass
    return _iter_rows(payload)


def _query_page_rows(
    client: httpx.Client,
    *,
    url: str,
    cookie: str,
    form_template: dict[str, str],
    page: int,
) -> list[dict[str, Any]]:
    payload = _request_jst(
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
    return _iter_rows(payload)


def _query_order_rows_by_identifier(
    client: httpx.Client,
    url: str,
    cookie: str,
    order_id: str | None,
    outer_order_id: str | None,
    identifier: str | None = None,
    form_template: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if identifier:
        rows = _query_order_rows(client, url, cookie, OUTER_ORDER_FILTER_KEY, identifier, form_template)
        if rows:
            return rows, OUTER_ORDER_FILTER_KEY
        for key in ORDER_ID_FILTER_KEYS:
            rows = _query_order_rows(client, url, cookie, key, identifier, form_template)
            if rows:
                return rows, key
        return [], OUTER_ORDER_FILTER_KEY
    if outer_order_id:
        return _query_order_rows(client, url, cookie, OUTER_ORDER_FILTER_KEY, outer_order_id, form_template), OUTER_ORDER_FILTER_KEY
    if not order_id:
        raise RuntimeError("请传入 --order-id 或 --outer-order-id")
    for key in ORDER_ID_FILTER_KEYS:
        rows = _query_order_rows(client, url, cookie, key, order_id, form_template)
        if rows:
            return rows, key
    return [], ORDER_ID_FILTER_KEYS[0]


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _parse_order_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("/", "-")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _resolve_query_date(value: str | None) -> tuple[date, str]:
    normalized = str(value or "today").strip().lower()
    if normalized in {"today", "now", "今日", "今天"}:
        today = datetime.now().astimezone().date()
        return today, today.isoformat()
    parsed = _parse_order_datetime(normalized)
    if parsed is None:
        raise RuntimeError("--date 仅支持 today 或 YYYY-MM-DD")
    return parsed.date(), parsed.date().isoformat()


def _contains_keyword(value: str, keyword: str | None) -> bool:
    if not keyword:
        return True
    return keyword.lower() in value.lower()


def _split_status_filter(status: str | None) -> list[str]:
    if not status:
        return []
    values = [part.strip() for part in re.split(r"[,，、;；]", status) if part.strip()]
    expanded: list[str] = []
    for value in values:
        aliases = STATUS_ALIASES.get(value, (value,))
        for alias in aliases:
            if alias not in expanded:
                expanded.append(alias)
    return expanded


def _matches_status(current_status: str, status: str | None) -> bool:
    allowed = _split_status_filter(status)
    if not allowed:
        return True
    return current_status in allowed


def _normalize_order_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    items = row.get("items")
    normalized: list[dict[str, Any]] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "product_code": _first_text(item, ORDER_ITEM_CODE_KEYS),
                    "product_name": _first_text(item, ORDER_ITEM_NAME_KEYS),
                }
            )
    if normalized:
        return normalized
    product_code = _first_text(row, ORDER_ITEM_CODE_KEYS)
    product_name = _first_text(row, ORDER_ITEM_NAME_KEYS)
    if not product_code and not product_name:
        return []
    return [{"product_code": product_code, "product_name": product_name}]


def _normalize_order_row(row: dict[str, Any]) -> dict[str, Any]:
    pay_dt = _parse_order_datetime(row.get("pay_date") or row.get("pay_time"))
    return {
        "order_id": _first_text(row, ("o_id", "so_id")),
        "outer_order_id": _first_text(row, ("outer_so_id", "raw_so_id", "pre_so_id", "so_id")),
        "shop_name": _first_text(row, ("shop_name", "store_name", "shop")),
        "status": _first_text(row, ("status", "order_status", "buyer_status")),
        "pay_time": pay_dt.isoformat(timespec="seconds") if pay_dt else str(row.get("pay_date") or row.get("pay_time") or "").strip(),
        "remark": _first_text(row, ORDER_REMARK_KEYS),
        "items": _normalize_order_items(row),
    }


def _matches_order_filters(
    row: dict[str, Any],
    *,
    target_date: date,
    shop_name: str | None,
    status: str | None,
    keyword: str | None,
) -> bool:
    pay_dt = _parse_order_datetime(row.get("pay_date") or row.get("pay_time"))
    if pay_dt is None or pay_dt.date() != target_date:
        return False
    current_shop = _first_text(row, ("shop_name", "store_name", "shop"))
    if shop_name and current_shop != shop_name:
        return False
    current_status = _first_text(row, ("status", "order_status", "buyer_status"))
    if not _matches_status(current_status, status):
        return False
    if keyword:
        haystacks = [
            _first_text(row, ("skus", "name", "item_name", "i_name")),
            *[
                " ".join(filter(None, (str(item.get("product_name") or "").strip(), str(item.get("product_code") or "").strip())))
                for item in _normalize_order_items(row)
            ],
        ]
        if not any(_contains_keyword(text, keyword) for text in haystacks if text):
            return False
    return True


def _guess_signed(status_text: str, trace_events: list[dict[str, Any]]) -> bool | None:
    combined = " ".join(
        [status_text]
        + [
            str(event.get("status") or event.get("desc") or event.get("text") or event.get("content") or "")
            for event in trace_events
            if isinstance(event, dict)
        ]
    ).lower()
    if not combined.strip():
        return None
    return any(keyword.lower() in combined for keyword in SIGNED_KEYWORDS)


def _normalize_trace_events(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        events: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                events.append(item)
            elif isinstance(item, str):
                events.append({"text": item})
        return events
    if isinstance(value, dict):
        for key in ("ReturnValue", "returnValue", "traces", "trace", "data", "items", "list", "routes", "steps", "result"):
            events = _normalize_trace_events(value.get(key))
            if events:
                return events
    return []


def _parse_acall_response(text: str) -> Any:
    separator_index = text.find("|")
    if 0 < separator_index < 6:
        offset = int(text[:separator_index] or "0")
        payload_text = text[separator_index + 1 + offset :]
        payload = json.loads(payload_text)
        if isinstance(payload, dict) and payload.get("IsSuccess") is False:
            returned = payload.get("ReturnValue")
            if isinstance(returned, dict) and returned.get("action") == "查询轨迹":
                raise LogisticsTraceAuthorizationRequired(
                    "查询轨迹需要完成短信验证。请先在聚水潭物流查询页面完成授权后重新执行。"
                )
            raise RuntimeError(str(payload.get("ExceptionMessage") or payload))
        return payload.get("ReturnValue") if isinstance(payload, dict) else payload
    return _extract_json_payload(text)


def _extract_hidden_inputs(page_html: str) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for tag in re.findall(r"<input[^>]*>", page_html, flags=re.I):
        name = re.search(r"name=[\"']([^\"']+)", tag, flags=re.I)
        value = re.search(r"value=[\"']([^\"']*)", tag, flags=re.I)
        if name:
            inputs[name.group(1)] = html.unescape(value.group(1) if value else "")
    return inputs


def _panel_headers(session: dict[str, Any]) -> dict[str, str]:
    raw_headers = {str(key): str(value) for key, value in (session.get("headers") or {}).items()}
    cookie = raw_headers.get("Cookie") or raw_headers.get("cookie") or ""
    return {
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": cookie,
        "Referer": "https://www.erp321.com/app/order/order/list.aspx",
        "User-Agent": raw_headers.get("user-agent") or raw_headers.get("User-Agent") or "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
    }


def _request_trace_from_panel(
    client: httpx.Client,
    session: dict[str, Any],
    logistics_no: str,
    company: str,
) -> list[dict[str, Any]]:
    if not logistics_no:
        return []
    base_url = "https://www.erp321.com/app/order/order/lookExpress.aspx"
    panel_url = f"{base_url}?l_id={quote(logistics_no)}&l_name={quote(company)}&outer_po_id="
    headers = _panel_headers(session)
    page_response = client.get(panel_url, headers=headers)
    page_response.raise_for_status()
    form = _extract_hidden_inputs(page_response.text)
    form["__CALLBACKID"] = "ACall1"
    form["__CALLBACKPARAM"] = json.dumps(
        {"Method": "LoadTrace", "CallControl": "{page}"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    response = client.post(f"{panel_url}&am___=LoadTrace", headers=headers, data=form)
    response.raise_for_status()
    return _normalize_trace_events(_parse_acall_response(response.text))


def resolve_logistics_from_row(
    client: httpx.Client,
    session: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    """Apply the shared order-logistics trace lookup to an already-fetched row."""
    logistics_no = _first_text(row, LOGISTICS_NUMBER_KEYS)
    company = _first_text(row, LOGISTICS_COMPANY_KEYS)
    status_text = _first_text(row, LOGISTICS_STATUS_KEYS)
    trace_events: list[dict[str, Any]] = []
    template = _load_logistics_template()
    trace_source = "order_list"
    if logistics_no:
        try:
            trace_events = _request_trace_from_panel(client, session, logistics_no, company)
            if trace_events:
                trace_source = "look_express_panel"
        except LogisticsTraceAuthorizationRequired:
            raise
        except Exception:
            trace_source = "look_express_panel_failed"
    if not trace_events and template and logistics_no:
        try:
            trace_events = _request_trace_from_template(client, template, logistics_no, row)
            if trace_events:
                trace_source = JST_ORDER_LOGISTICS_SCENE
        except LogisticsTraceAuthorizationRequired:
            raise
        except Exception:
            trace_source = "order_list_template_failed"
    if not status_text and trace_events:
        first_event = trace_events[0]
        status_text = str(first_event.get("StatusSrc") or first_event.get("status") or "").strip()
    return {
        "logistics_no": logistics_no,
        "logistics_company": company,
        "logistics_status": status_text,
        "signed": _guess_signed(status_text, trace_events),
        "trace_source": trace_source,
        "trace_events": trace_events,
        "logistics_scene": JST_ORDER_LOGISTICS_SCENE if template else None,
        "next_learn_command": None if template else "ops --json jst order logistics learn --outer-order-id <订单号>",
    }


def _build_logistics_template(*, captured: dict[str, Any]) -> Path:
    template = {
        "site": JST_SITE,
        "scene": JST_ORDER_LOGISTICS_SCENE,
        "capture_source": "sessionhub_9222",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "url": captured.get("url"),
        "method": captured.get("method"),
        "headers": _merge_cookie_header(
            dict(captured.get("headers") or {}),
            captured.get("cookies") or [],
        ),
        "post_data": captured.get("post_data"),
        "post_data_json": captured.get("post_data_json"),
        "defaults": {
            "order_page_scene": JST_ORDER_SCENE,
            "logistics_number_keys": list(LOGISTICS_NUMBER_KEYS),
            "status_keywords": list(SIGNED_KEYWORDS),
        },
    }
    template_path = _template_path()
    _write_json(template_path, template)
    scene_path = _scene_store_path(JST_SITE, JST_ORDER_LOGISTICS_SCENE)
    _write_json(scene_path, {**captured, "site": JST_SITE, "scene": JST_ORDER_LOGISTICS_SCENE})
    return template_path


def _load_logistics_template() -> dict[str, Any] | None:
    path = _template_path()
    if not path.exists():
        return None
    return _read_json(path)


def _request_trace_from_template(client: httpx.Client, template: dict[str, Any], logistics_no: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(template.get("url") or "").strip()
    if not url:
        return []
    method = str(template.get("method") or "POST").upper()
    headers = dict(template.get("headers") or {})
    post_data_json = template.get("post_data_json")
    post_data = template.get("post_data")
    body: Any = None
    json_body: Any = None
    if isinstance(post_data_json, dict):
        json_body = json.loads(json.dumps(post_data_json).replace("__LOGISTICS_NO__", logistics_no).replace("__O_ID__", str(row.get("o_id") or "")))
    elif isinstance(post_data, str):
        body = post_data.replace("__LOGISTICS_NO__", logistics_no).replace("__O_ID__", str(row.get("o_id") or ""))
    response = client.request(method, url, headers=headers, json=json_body, content=body)
    response.raise_for_status()
    try:
        payload = response.json()
    except Exception:
        payload = _extract_json_payload(response.text)
    return _normalize_trace_events(payload)


def _probe_primary_chrome(order_id: str | None, outer_order_id: str | None) -> dict[str, Any]:
    cdp_url = get_config().primary_chrome_cdp_url.strip()
    if not cdp_url:
        return {"status": "skipped", "reason": "PRIMARY_CHROME_CDP_URL 未配置"}

    from playwright.sync_api import Error as PlaywrightError  # type: ignore
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore

    captured: dict[str, Any] | None = None
    keyword_parts = [part for part in (order_id, outer_order_id) if part]

    def on_request(request: Any) -> None:
        nonlocal captured
        if captured is not None or request.method.upper() not in {"GET", "POST"}:
            return
        target_text = f"{request.url} {request.post_data or ''}".lower()
        if not any(keyword in target_text for keyword in ("logistic", "logistics", "express", "trace", "track", "kuaidi", "快递", "物流")):
            return
        captured = {
            "status": "captured",
            "source": "primary_chrome",
            "url": request.url,
            "method": request.method.upper(),
            "headers": dict(request.headers),
            "post_data": request.post_data,
            "meta": {"captured_at": datetime.now().isoformat(timespec="seconds")},
        }

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as exc:
            return {"status": "unavailable", "source": "primary_chrome", "cdp_url": cdp_url, "reason": str(exc)}
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        context.on("request", on_request)
        try:
            page.goto("https://www.erp321.com/app/order/order/list.aspx", wait_until="domcontentloaded", timeout=20000)
        except PlaywrightTimeoutError:
            pass
        page.wait_for_timeout(1500)
        for text in keyword_parts:
            try:
                locator = page.get_by_text(text, exact=False).first
                if locator.count():
                    locator.click(timeout=2000)
                    break
            except Exception:
                pass
        for text in ("物流", "快递", "快递单号", "物流跟踪", "物流详情"):
            try:
                page.get_by_text(text, exact=False).first.click(timeout=2000)
                break
            except Exception:
                continue
        deadline = time.time() + 12
        while captured is None and time.time() < deadline:
            page.wait_for_timeout(500)
    if captured:
        return captured
    return {"status": "not_captured", "source": "primary_chrome", "cdp_url": cdp_url, "reason": "主浏览器未捕获到物流请求，继续用 9222 SessionHub 正式沉淀"}


def _capture_logistics_scene(order_id: str | None, outer_order_id: str | None, primary_probe: dict[str, Any] | None = None) -> dict[str, Any]:
    get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
    root = _sessionhub_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from scene.chrome_cdp import CDP_URL, start_chrome  # type: ignore
    from playwright.sync_api import Error as PlaywrightError  # type: ignore
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore

    ok, msg = start_chrome()
    if not ok:
        raise RuntimeError(msg)

    captured: dict[str, Any] | None = None
    keyword_parts = [part for part in (order_id, outer_order_id) if part]

    def on_request(request: Any) -> None:
        nonlocal captured
        if captured is not None:
            return
        if request.method.upper() not in {"GET", "POST"}:
            return
        target_text = f"{request.url} {request.post_data or ''}".lower()
        if not any(keyword in target_text for keyword in ("logistic", "logistics", "express", "trace", "track", "kuaidi", "快递", "物流")):
            return
        captured = {
            "status": "captured",
            "source": "sessionhub_9222",
            "url": request.url,
            "method": request.method.upper(),
            "headers": dict(request.headers),
            "post_data": request.post_data,
            "post_data_json": request.post_data_json if request.post_data else None,
            "post_data_form": None,
            "cookies": [],
            "tokens": {},
            "meta": {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "target_order_id": order_id,
                "target_outer_order_id": outer_order_id,
                "discovery_strategy": "primary_chrome_probe_then_sessionhub_9222_capture",
                "primary_probe": primary_probe or {},
            },
        }

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        with managed_work_page(context, "jst.order.logistics") as page:
            context.on("request", on_request)
            try:
                page.goto("https://www.erp321.com/app/order/order/list.aspx", wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                pass
            surface_jst_login_if_needed(page)
            page.wait_for_timeout(2000)
            for text in keyword_parts:
                try:
                    locator = page.get_by_text(text, exact=False).first
                    if locator.count():
                        locator.click(timeout=3000)
                        break
                except Exception:
                    pass
            for text in ("物流", "快递", "快递单号", "物流跟踪", "物流详情"):
                try:
                    page.get_by_text(text, exact=False).first.click(timeout=3000)
                    break
                except Exception:
                    continue
            deadline = time.time() + 45
            while captured is None and time.time() < deadline:
                page.wait_for_timeout(1000)
            if captured is None:
                raise RuntimeError("未捕获到物流请求。请确认 9222 Chrome 已登录聚水潭，且订单列表页能点击快递单号查看物流。")
            try:
                captured["cookies"] = context.cookies([captured["url"]])
            except Exception:
                captured["cookies"] = context.cookies()

    return captured


def learn_order_logistics(*, order_id: str | None = None, outer_order_id: str | None = None) -> CommandResponse:
    primary_probe = _probe_primary_chrome(order_id=order_id, outer_order_id=outer_order_id)
    captured = _capture_logistics_scene(order_id=order_id, outer_order_id=outer_order_id, primary_probe=primary_probe)
    template_path = _build_logistics_template(captured=captured)
    scene_path = _scene_store_path(JST_SITE, JST_ORDER_LOGISTICS_SCENE)
    context_path = write_runtime_context(
        task_name="jst_order_logistics_learn",
        status="success",
        inputs={"order_id": order_id, "outer_order_id": outer_order_id},
        outputs={
            "scene_path": str(scene_path),
            "template_path": str(template_path),
            "url": captured.get("url"),
            "method": captured.get("method"),
            "primary_probe": primary_probe,
        },
        artifacts=[str(scene_path), str(template_path)],
    )
    return CommandResponse(
        success=True,
        platform="jst",
        command="order logistics learn",
        data={
            "site": JST_SITE,
            "scene": JST_ORDER_LOGISTICS_SCENE,
            "source": "sessionhub_9222",
            "primary_probe": primary_probe,
            "scene_path": str(scene_path),
            "template_path": str(template_path),
            "context_path": str(context_path),
            "next_command": "ops --json jst order logistics --outer-order-id <订单号>",
        },
    )


def _run_single_order_logistics(
    client: httpx.Client,
    *,
    session: dict[str, Any],
    url: str,
    cookie: str,
    form_template: dict[str, str],
    order_id: str | None = None,
    outer_order_id: str | None = None,
    identifier: str | None = None,
) -> dict[str, Any]:
    rows, filter_key = _query_order_rows_by_identifier(
        client,
        url,
        cookie,
        order_id,
        outer_order_id,
        identifier=identifier,
        form_template=form_template,
    )
    if not rows:
        raise RuntimeError("聚水潭未找到指定订单")
    if len(rows) > 1:
        raise RuntimeError(f"聚水潭返回 {len(rows)} 条订单，请换更精确的订单号")

    row = rows[0]
    logistics = resolve_logistics_from_row(client, session, row)
    return {
        "order_id": order_id,
        "outer_order_id": outer_order_id,
        "identifier": identifier,
        "matched_filter": filter_key,
        "o_id": str(row.get("o_id") or ""),
        "so_id": str(row.get("so_id") or ""),
        "raw_so_id": str(row.get("raw_so_id") or ""),
        "pre_so_id": str(row.get("pre_so_id") or ""),
        "logistics_no": logistics["logistics_no"],
        "logistics_company": logistics["logistics_company"],
        "logistics_status": logistics["logistics_status"],
        "signed": logistics["signed"],
        "send_date": str(row.get("send_date") or ""),
        "plan_delivery_date": str(row.get("plan_delivery_date") or ""),
        "sign_time": str(row.get("sign_time") or ""),
        "receiver_area": " ".join(
            str(row.get(key) or "").strip()
            for key in ("receiver_state", "receiver_city", "receiver_district")
            if str(row.get(key) or "").strip()
        ),
        "trace_source": logistics["trace_source"],
        "trace_events": logistics["trace_events"],
        "scene": JST_ORDER_SCENE,
        "logistics_scene": logistics["logistics_scene"],
        "next_learn_command": logistics["next_learn_command"],
    }


def run_order_logistics(
    *,
    order_id: str | None = None,
    outer_order_id: str | None = None,
    order_ids: list[str] | None = None,
    outer_order_ids: list[str] | None = None,
    input_path: str | None = None,
    limit: int | None = None,
) -> CommandResponse:
    explicit_order_ids = _normalize_identifier_list(order_ids or order_id)
    explicit_outer_order_ids = _normalize_identifier_list(outer_order_ids or outer_order_id)

    if not explicit_order_ids and not explicit_outer_order_ids and not input_path:
        raise RuntimeError("请传入 --order-id、--outer-order-id 或 --input")

    input_orders: list[str] = []
    resolved_input: str | None = None
    if input_path:
        input_orders, resolved_input = _normalize_orders(order_ids=[], input_path=input_path, limit=None)
    requests: list[dict[str, str]] = []
    requests.extend({"order_id": value} for value in explicit_order_ids)
    requests.extend({"outer_order_id": value} for value in explicit_outer_order_ids)
    requests.extend({"identifier": value} for value in input_orders)

    if limit is not None:
        if limit <= 0:
            raise RuntimeError("--limit 必须大于 0")
        requests = requests[:limit]

    if not requests:
        raise RuntimeError("未找到可查询的订单号")

    is_single_query = len(requests) == 1 and resolved_input is None
    if is_single_query:
        session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
        headers = dict(session.get("headers") or {})
        cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
        if not cookie:
            raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")
        url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
        form_template = _extract_form_template(session)
        with build_client(follow_redirects=True, timeout=60.0) as client:
            item = _run_single_order_logistics(
                client,
                session=session,
                url=url,
                cookie=cookie,
                form_template=form_template,
                order_id=requests[0].get("order_id"),
                outer_order_id=requests[0].get("outer_order_id"),
            )
        context_path = write_runtime_context(
            task_name="jst_order_logistics_run",
            status="success",
            inputs={
                "order_id": item.get("order_id"),
                "outer_order_id": item.get("outer_order_id"),
                "filter_key": item.get("matched_filter"),
            },
            outputs={
                "logistics_no": item.get("logistics_no"),
                "company": item.get("logistics_company"),
                "status": item.get("logistics_status"),
                "signed": item.get("signed"),
                "trace_count": len(item.get("trace_events") or []),
            },
        )
        item["context_path"] = str(context_path)
        return CommandResponse(
            success=True,
            platform="jst",
            command="order logistics",
            data=item,
        )

    retried_for_auth = False
    auth_refresh_applied = False
    while True:
        session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
        headers = dict(session.get("headers") or {})
        cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
        if not cookie:
            raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")
        url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
        form_template = _extract_form_template(session)
        results: list[dict[str, Any]] = []

        with build_client(follow_redirects=True, timeout=60.0) as client:
            for request in requests:
                try:
                    item = _run_single_order_logistics(
                        client,
                        session=session,
                        url=url,
                        cookie=cookie,
                        form_template=form_template,
                        order_id=request.get("order_id"),
                        outer_order_id=request.get("outer_order_id"),
                        identifier=request.get("identifier"),
                    )
                    item["success"] = True
                    results.append(item)
                except Exception as exc:
                    reason = str(exc)
                    results.append(
                        {
                            "order_id": request.get("order_id"),
                            "outer_order_id": request.get("outer_order_id"),
                            "identifier": request.get("identifier"),
                            "success": False,
                            "error": reason,
                            "auth_required": is_probable_auth_error(reason),
                        }
                    )

        auth_failures = [row for row in results if row.get("auth_required")]
        if auth_failures and not retried_for_auth and not any(row.get("success") for row in results):
            require_interactive_recovery(JST_ORDER_SCENE)
            get_scene_manager().capture_scene(JST_SITE, JST_ORDER_SCENE)
            mark_scene_refreshed(JST_ORDER_SCENE)
            retried_for_auth = True
            auth_refresh_applied = True
            continue
        break

    success_count = sum(1 for row in results if row.get("success"))
    failed_count = len(results) - success_count
    context_path = write_runtime_context(
        task_name="jst_order_logistics_batch_run",
        status="success" if failed_count == 0 else "partial_success",
        inputs={
            "order_ids": explicit_order_ids,
            "outer_order_ids": explicit_outer_order_ids,
            "input_path": resolved_input,
            "limit": limit,
        },
        outputs={"total": len(results), "success": success_count, "failed": failed_count},
    )
    data: dict[str, Any] = {
        "site": JST_SITE,
        "scene": JST_ORDER_SCENE,
        "session_source": session.get("source", "sessionhub"),
        "input_path": resolved_input,
        "summary": {
            "total": len(results),
            "success": success_count,
            "failed": failed_count,
        },
        "items": results,
        "context_path": str(context_path),
    }
    if auth_refresh_applied:
        data["auth_refresh_applied"] = True
    return CommandResponse(
        success=failed_count == 0,
        platform="jst",
        command="order logistics",
        data=data,
    )


def run_order_query(
    *,
    date_value: str = "today",
    order_ids: list[str] | None = None,
    shop_name: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    limit: int | None = None,
) -> CommandResponse:
    if limit is not None and limit <= 0:
        raise RuntimeError("--limit 必须大于 0")

    target_date, resolved_date = _resolve_query_date(date_value)
    session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
    headers = dict(session.get("headers") or {})
    cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
    if not cookie:
        raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")
    url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
    form_template = _extract_form_template(session)
    max_pages = int(os.getenv("JST_ORDER_QUERY_MAX_PAGES", "100"))
    orders: list[dict[str, Any]] = []
    query_order_ids = _normalize_identifier_list(order_ids)

    with build_client(follow_redirects=True, timeout=60.0) as client:
        if query_order_ids:
            rows: list[dict[str, Any]] = []
            for order_id in query_order_ids:
                matched_rows, _ = _query_order_rows_by_identifier(
                    client,
                    url,
                    cookie,
                    order_id=order_id,
                    outer_order_id=None,
                    form_template=form_template,
                )
                rows.extend(matched_rows)
            for row in rows:
                if not _matches_order_filters(
                    row,
                    target_date=target_date,
                    shop_name=shop_name,
                    status=status,
                    keyword=keyword,
                ):
                    continue
                orders.append(_normalize_order_row(row))
                if limit is not None and len(orders) >= limit:
                    break
        else:
            for page in range(1, max_pages + 1):
                rows = _query_page_rows(client, url=url, cookie=cookie, form_template=form_template, page=page)
                if not rows:
                    break
                oldest_pay_dt: datetime | None = None
                for row in rows:
                    pay_dt = _parse_order_datetime(row.get("pay_date") or row.get("pay_time"))
                    if pay_dt is not None and (oldest_pay_dt is None or pay_dt < oldest_pay_dt):
                        oldest_pay_dt = pay_dt
                    if not _matches_order_filters(
                        row,
                        target_date=target_date,
                        shop_name=shop_name,
                        status=status,
                        keyword=keyword,
                    ):
                        continue
                    orders.append(_normalize_order_row(row))
                    if limit is not None and len(orders) >= limit:
                        break
                if limit is not None and len(orders) >= limit:
                    break
                if oldest_pay_dt is not None and oldest_pay_dt.date() < target_date:
                    break

    context_path = write_runtime_context(
        task_name="jst_order_query_run",
        status="success",
        inputs={
            "date": resolved_date,
            "order_ids": query_order_ids,
            "shop_name": shop_name,
            "status": status,
            "keyword": keyword,
            "limit": limit,
        },
        outputs={"total_orders": len(orders)},
    )
    return CommandResponse(
        success=True,
        platform="jst",
        command="order query",
        data={
            "site": JST_SITE,
            "scene": JST_ORDER_SCENE,
            "session_source": session.get("source", "sessionhub"),
            "filters": {
                "date": resolved_date,
                "order_ids": query_order_ids,
                "shop_name": shop_name,
                "status": status,
                "keyword": keyword,
                "limit": limit,
            },
            "summary": {"total": len(orders)},
            "orders": orders,
            "context_path": str(context_path),
        },
    )


def run_order_label(
    *,
    order_ids: list[str],
    input_path: str | None,
    limit: int | None,
    execute: bool,
    labels: str,
    remark_text: str,
) -> CommandResponse:
    orders, resolved_input = _normalize_orders(order_ids=order_ids, input_path=input_path, limit=limit)
    mode = "execute" if execute else "dry-run"
    retried_for_auth = False
    auth_refresh_applied = False
    while True:
        session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
        headers = dict(session.get("headers") or {})
        cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
        if not cookie:
            raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")

        url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
        form_template = _extract_form_template(session)
        results: list[dict[str, Any]] = []

        with build_client(follow_redirects=True) as client:
            for order_no in orders:
                try:
                    matched_o_ids = _query_order_o_ids(client, url, cookie, order_no, form_template)
                    if not matched_o_ids:
                        results.append({"order_no": order_no, "status": "failed_not_found", "reason": "聚水潭未找到订单"})
                        continue
                    if len(matched_o_ids) > 1:
                        results.append(
                            {
                                "order_no": order_no,
                                "status": "failed_multi",
                                "reason": f"聚水潭返回 {len(matched_o_ids)} 条记录",
                                "matched_o_ids": matched_o_ids,
                            }
                        )
                        continue

                    o_id = matched_o_ids[0]
                    if execute:
                        _append_remark(client, url, cookie, o_id, remark_text, form_template, remark_type=LABEL_REMARK_TYPE)
                        _set_labels(client, url, cookie, o_id, labels, form_template)
                    results.append({"order_no": order_no, "status": "success", "o_id": o_id})
                except httpx.HTTPError as exc:
                    results.append({"order_no": order_no, "status": "failed_request", "reason": str(exc)})
                except Exception as exc:
                    results.append({"order_no": order_no, "status": "failed_error", "reason": str(exc)})

        auth_failures = [row for row in results if row["status"] == "failed_request" and is_probable_auth_error(row.get("reason"))]
        if auth_failures and not retried_for_auth and not any(row["status"] == "success" for row in results):
            require_interactive_recovery(JST_ORDER_SCENE)
            get_scene_manager().capture_scene(JST_SITE, JST_ORDER_SCENE)
            mark_scene_refreshed(JST_ORDER_SCENE)
            retried_for_auth = True
            auth_refresh_applied = True
            continue
        break

    failed_output = _write_failed_orders(results)
    success_count = sum(1 for row in results if row["status"] == "success")
    not_found_count = sum(1 for row in results if row["status"] == "failed_not_found")
    multi_count = sum(1 for row in results if row["status"] == "failed_multi")
    failed_count = sum(1 for row in results if row["status"] not in {"success", "failed_not_found", "failed_multi"})

    data: dict[str, Any] = {
        "mode": mode,
        "site": JST_SITE,
        "scene": JST_ORDER_SCENE,
        "session_source": session.get("source", "sessionhub"),
        "input_path": resolved_input,
        "labels": labels,
        "remark_text": remark_text,
        "summary": {
            "total": len(results),
            "success": success_count,
            "not_found": not_found_count,
            "multi_match": multi_count,
            "failed": failed_count,
        },
        "results": results,
    }
    if auth_refresh_applied:
        data["auth_refresh_applied"] = True
    if failed_output:
        data["failed_output"] = failed_output

    return CommandResponse(
        success=True,
        platform="jst",
        command="order label",
        data=data,
    )


def run_order_remark(
    *,
    order_ids: list[str],
    input_path: str | None,
    limit: int | None,
    execute: bool,
    remark_text: str,
) -> CommandResponse:
    if not order_ids and not input_path:
        raise RuntimeError("请传入 --order-id 或 --input")
    orders, resolved_input = _normalize_orders(order_ids=order_ids, input_path=input_path, limit=limit)
    if not remark_text.strip():
        raise RuntimeError("--remark-text 不能为空")

    mode = "execute" if execute else "dry-run"
    retried_for_auth = False
    auth_refresh_applied = False
    while True:
        session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
        headers = dict(session.get("headers") or {})
        cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
        if not cookie:
            raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")

        url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
        form_template = _extract_form_template(session)
        results: list[dict[str, Any]] = []

        with build_client(follow_redirects=True) as client:
            for order_no in orders:
                try:
                    rows, _ = _query_order_rows_by_identifier(
                        client,
                        url,
                        cookie,
                        order_id=order_no,
                        outer_order_id=None,
                        form_template=form_template,
                    )
                    matched_o_ids = [
                        text
                        for text in (str(row.get("o_id") or "").strip() for row in rows)
                        if text
                    ]
                    if not matched_o_ids:
                        results.append({"order_no": order_no, "status": "failed_not_found", "reason": "聚水潭未找到订单"})
                        continue
                    if len(matched_o_ids) > 1:
                        results.append(
                            {
                                "order_no": order_no,
                                "status": "failed_multi",
                                "reason": f"聚水潭返回 {len(matched_o_ids)} 条记录",
                                "matched_o_ids": matched_o_ids,
                            }
                        )
                        continue

                    o_id = matched_o_ids[0]
                    if execute:
                        _append_remark(client, url, cookie, o_id, remark_text, form_template)
                    results.append({"order_no": order_no, "status": "success", "o_id": o_id})
                except httpx.HTTPError as exc:
                    results.append({"order_no": order_no, "status": "failed_request", "reason": str(exc)})
                except Exception as exc:
                    results.append({"order_no": order_no, "status": "failed_error", "reason": str(exc)})

        auth_failures = [row for row in results if row["status"] == "failed_request" and is_probable_auth_error(row.get("reason"))]
        if auth_failures and not retried_for_auth and not any(row["status"] == "success" for row in results):
            require_interactive_recovery(JST_ORDER_SCENE)
            get_scene_manager().capture_scene(JST_SITE, JST_ORDER_SCENE)
            mark_scene_refreshed(JST_ORDER_SCENE)
            retried_for_auth = True
            auth_refresh_applied = True
            continue
        break

    failed_output = _write_failed_orders(results, prefix="jst_remark_failed_orders")
    success_count = sum(1 for row in results if row["status"] == "success")
    not_found_count = sum(1 for row in results if row["status"] == "failed_not_found")
    multi_count = sum(1 for row in results if row["status"] == "failed_multi")
    failed_count = sum(1 for row in results if row["status"] not in {"success", "failed_not_found", "failed_multi"})

    data: dict[str, Any] = {
        "mode": mode,
        "site": JST_SITE,
        "scene": JST_ORDER_SCENE,
        "session_source": session.get("source", "sessionhub"),
        "input_path": resolved_input,
        "remark_text": remark_text,
        "summary": {
            "total": len(results),
            "success": success_count,
            "not_found": not_found_count,
            "multi_match": multi_count,
            "failed": failed_count,
        },
        "results": results,
    }
    if auth_refresh_applied:
        data["auth_refresh_applied"] = True
    if failed_output:
        data["failed_output"] = failed_output

    return CommandResponse(
        success=True,
        platform="jst",
        command="order remark",
        data=data,
    )


def run_order_normalize(
    *,
    order_ids: list[str],
    input_path: str | None,
    limit: int | None,
    execute: bool,
) -> CommandResponse:
    """异常转正常单：对传入订单执行订单列表「转正常单」(UnQuestions)。

    与 run_order_remark 同构：按订单号解析唯一 o_id，--execute 才真正调用；沿用
    SessionHub 会话与 auth 失效重沉淀。仅清除异常标记，不改其它字段。
    """
    if not order_ids and not input_path:
        raise RuntimeError("请传入 --order-id 或 --input")
    orders, resolved_input = _normalize_orders(order_ids=order_ids, input_path=input_path, limit=limit)

    mode = "execute" if execute else "dry-run"
    retried_for_auth = False
    auth_refresh_applied = False
    while True:
        session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
        headers = dict(session.get("headers") or {})
        cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
        if not cookie:
            raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")

        url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
        form_template = _extract_form_template(session)
        results: list[dict[str, Any]] = []

        with build_client(follow_redirects=True) as client:
            for order_no in orders:
                try:
                    rows, _ = _query_order_rows_by_identifier(
                        client,
                        url,
                        cookie,
                        order_id=order_no,
                        outer_order_id=None,
                        form_template=form_template,
                    )
                    matched_o_ids = [
                        text
                        for text in (str(row.get("o_id") or "").strip() for row in rows)
                        if text
                    ]
                    if not matched_o_ids:
                        results.append({"order_no": order_no, "status": "failed_not_found", "reason": "聚水潭未找到订单"})
                        continue
                    if len(matched_o_ids) > 1:
                        results.append(
                            {
                                "order_no": order_no,
                                "status": "failed_multi",
                                "reason": f"聚水潭返回 {len(matched_o_ids)} 条记录",
                                "matched_o_ids": matched_o_ids,
                            }
                        )
                        continue

                    o_id = matched_o_ids[0]
                    if execute:
                        _set_normal(client, url, cookie, o_id, form_template)
                    results.append({"order_no": order_no, "status": "success", "o_id": o_id})
                except httpx.HTTPError as exc:
                    results.append({"order_no": order_no, "status": "failed_request", "reason": str(exc)})
                except Exception as exc:
                    results.append({"order_no": order_no, "status": "failed_error", "reason": str(exc)})

        auth_failures = [row for row in results if row["status"] == "failed_request" and is_probable_auth_error(row.get("reason"))]
        if auth_failures and not retried_for_auth and not any(row["status"] == "success" for row in results):
            require_interactive_recovery(JST_ORDER_SCENE)
            get_scene_manager().capture_scene(JST_SITE, JST_ORDER_SCENE)
            mark_scene_refreshed(JST_ORDER_SCENE)
            retried_for_auth = True
            auth_refresh_applied = True
            continue
        break

    failed_output = _write_failed_orders(results, prefix="jst_normalize_failed_orders")
    success_count = sum(1 for row in results if row["status"] == "success")
    not_found_count = sum(1 for row in results if row["status"] == "failed_not_found")
    multi_count = sum(1 for row in results if row["status"] == "failed_multi")
    failed_count = sum(1 for row in results if row["status"] not in {"success", "failed_not_found", "failed_multi"})

    data: dict[str, Any] = {
        "mode": mode,
        "site": JST_SITE,
        "scene": JST_ORDER_SCENE,
        "session_source": session.get("source", "sessionhub"),
        "input_path": resolved_input,
        "summary": {
            "total": len(results),
            "success": success_count,
            "not_found": not_found_count,
            "multi_match": multi_count,
            "failed": failed_count,
        },
        "results": results,
    }
    if auth_refresh_applied:
        data["auth_refresh_applied"] = True
    if failed_output:
        data["failed_output"] = failed_output

    return CommandResponse(
        success=True,
        platform="jst",
        command="order normalize",
        data=data,
    )
