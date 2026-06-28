from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.output import CommandResponse
from ops_cli.platforms.jst.order import DEFAULT_JST_ORDER_PATH
from ops_cli.platforms.jst.order import JST_ORDER_SCENE
from ops_cli.platforms.jst.order import JST_SITE
from ops_cli.platforms.jst.order import ORDER_ID_FILTER_KEYS
from ops_cli.platforms.jst.order import OUTER_ORDER_FILTER_KEY
from ops_cli.platforms.jst.order import _extract_form_template
from ops_cli.platforms.jst.order import _first_text
from ops_cli.platforms.jst.order import _query_order_rows
from ops_cli.platforms.jst.order import _query_order_rows_by_identifier
from ops_cli.runtime_context import write_runtime_context
from ops_cli.utils.http import build_client


JST_ORDER_INVOICE_SCENE = "order_invoice_workorder"
TEMPLATE_PATH = Path("data/jst/order_invoice_workorder_template.json")
BATCH_INSERT_URL = "https://api.erp321.com/jgd/api/gd/workOrder/batchInsert"
WORKORDER_TYPE_LIST_URL = "https://api.erp321.com/jgd/api/gd/workOrder/woTypeList"
WORKORDER_TYPE_DETAIL_URL = "https://api.erp321.com/jgd/api/gd/workOrder/woTypeDetail"
WORKORDER_ORIGIN = "https://shouhou.erp321.com"
WORKORDER_REFERER = "https://shouhou.erp321.com/"
WORKORDER_TITLE = "发票"
DEFAULT_INVOICE_TYPE = "专用发票"
DEFAULT_QUANTITY = 1
DEFAULT_WORKORDER_TYPE_ID = "5821A58E9D39459DBC4E87569A9A6D68"
DEFAULT_WORKORDER_SOURCE = "PC_ORDER"
DEFAULT_GRADE = "NORMAL"
DEFAULT_FIELD_MAP = {
    "invoice_type": "SELECTField1023127320230406160017",
    "shop_name": "shopField",
    "invoice_entity": "SELECTField1023127320240724093511",
    "title": "TEXTField1023127320230406155805",
    "tax_no": "TEXTField1023127320230406161929",
    "address": "TEXTField1023127320230406155824",
    "phone": "TEXTField1023127320230406155824",
    "bank": "TEXTField1023127320230406155815",
    "bank_account": "TEXTField1023127320230406155815",
    "quantity": "itemsCount",
    "amount": "actualPaidAmountField",
    "order": "orderField",
}
FIELD_NAME_ALIASES = {
    "invoice_type": ["发票类型"],
    "shop_name": ["店铺名称"],
    "invoice_entity": ["开票主体公司"],
    "title": ["抬头"],
    "tax_no": ["税号"],
    "address": ["公司地址、公司电话（专票使用）", "地址", "公司地址"],
    "phone": ["公司地址、公司电话（专票使用）", "电话", "公司电话"],
    "bank": ["开户行、开户账号（专票使用）", "开户行"],
    "bank_account": ["开户行、开户账号（专票使用）", "开户账号"],
    "quantity": ["商品数量"],
    "amount": ["实付金额", "打款金额"],
    "order": ["线上订单号", "LP线上订单号"],
}


def _template_path() -> Path:
    return Path.cwd() / TEMPLATE_PATH


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _money(value: str | Decimal) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("--amount 必须是合法金额") from exc
    if amount <= 0:
        raise RuntimeError("--amount 必须大于 0")
    return amount.quantize(Decimal("0.01"))


def _validate_required(**values: str) -> None:
    missing = [name for name, value in values.items() if not str(value or "").strip()]
    if missing:
        raise RuntimeError(f"缺少必填发票信息：{', '.join(missing)}")


def _extract_cookie_value(headers: dict[str, str], name: str) -> str:
    cookie = headers.get("Cookie") or headers.get("cookie") or ""
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key == name:
            return value
    return ""


def _api_headers(session: dict[str, Any], template_headers: dict[str, Any] | None = None) -> dict[str, str]:
    source_headers = {str(key): str(value) for key, value in (session.get("headers") or {}).items()}
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": WORKORDER_ORIGIN,
        "Referer": WORKORDER_REFERER,
    }
    for key, value in (template_headers or {}).items():
        key_text = str(key)
        if key_text.lower() in {"authorization", "cookie", "content-length", "host"}:
            continue
        headers[key_text] = str(value)
    for key in ("User-Agent", "user-agent", "X-Requested-With", "x-requested-with"):
        value = source_headers.get(key)
        if value:
            canonical = "-".join(part.capitalize() for part in key.split("-"))
            headers[canonical] = value
    u_sso_token = _extract_cookie_value(source_headers, "u_sso_token")
    if u_sso_token:
        headers["u_sso_token"] = u_sso_token
    return headers


def _session_identity(session: dict[str, Any]) -> dict[str, str]:
    source_headers = {str(key): str(value) for key, value in (session.get("headers") or {}).items()}
    uid = _extract_cookie_value(source_headers, "u_id")
    coid = _extract_cookie_value(source_headers, "u_co_id")
    if not uid or not coid:
        return {"uid": "", "coid": "", "ip": ""}
    return {"uid": uid, "coid": coid, "ip": ""}


def _online_order_id(row: dict[str, Any]) -> str:
    for key in ("so_id", "raw_so_id", "pre_so_id"):
        value = str(row.get(key) or "").strip()
        if value.startswith("LP"):
            return value
    return _first_text(row, ("so_id", "raw_so_id", "pre_so_id"))


def _resolve_order_identity(order_id: str, outer_order_id: str | None = None) -> dict[str, str]:
    session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
    headers = dict(session.get("headers") or {})
    cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
    if not cookie:
        raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")
    url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
    form_template = _extract_form_template(session)

    with build_client(follow_redirects=True, timeout=60.0) as client:
        rows, filter_key = _query_order_rows_by_identifier(
            client,
            url,
            cookie,
            order_id,
            outer_order_id,
            form_template=form_template,
        )
        if not rows and order_id and not outer_order_id:
            rows = _query_order_rows(client, url, cookie, OUTER_ORDER_FILTER_KEY, order_id, form_template)
            filter_key = OUTER_ORDER_FILTER_KEY
    if not rows:
        raise RuntimeError("聚水潭未找到指定订单")
    if len(rows) > 1:
        raise RuntimeError(f"聚水潭返回 {len(rows)} 条订单，请换更精确的订单号")
    row = rows[0]
    internal_order_id = str(row.get("o_id") or "").strip()
    online_order_id = _online_order_id(row)
    if not internal_order_id:
        raise RuntimeError("聚水潭订单缺少 o_id，无法创建工单")
    return {
        "order_id": order_id,
        "outer_order_id": outer_order_id or "",
        "matched_filter": filter_key,
        "internal_order_id": internal_order_id,
        "online_order_id": online_order_id,
    }


def _merge_field_value(field_key: str, fields: dict[str, str], values: dict[str, Any]) -> Any:
    value = values[field_key]
    field_id = str(fields.get(field_key) or "").strip()
    if field_key == "address" and field_id and field_id == str(fields.get("phone") or "").strip():
        return " ".join(part for part in (str(values["address"]).strip(), str(values["phone"]).strip()) if part)
    if field_key == "bank" and field_id and field_id == str(fields.get("bank_account") or "").strip():
        return " ".join(part for part in (str(values["bank"]).strip(), str(values["bank_account"]).strip()) if part)
    return value


def build_invoice_workorder_payload(
    *,
    order_id: str,
    internal_order_id: str,
    online_order_id: str,
    invoice_type: str,
    shop_name: str,
    invoice_entity: str,
    title: str,
    tax_no: str,
    address: str,
    phone: str,
    bank: str,
    bank_account: str,
    amount: Decimal,
    quantity: int,
    field_map: dict[str, str] | None = None,
    workorder_type_id: str = DEFAULT_WORKORDER_TYPE_ID,
) -> dict[str, Any]:
    fields = {**DEFAULT_FIELD_MAP, **(field_map or {})}
    begin_time = datetime.now().replace(microsecond=0)
    end_time = begin_time + timedelta(days=3)
    values: dict[str, Any] = {
        "invoice_type": invoice_type,
        "shop_name": shop_name,
        "invoice_entity": invoice_entity,
        "title": title,
        "tax_no": tax_no,
        "address": address,
        "phone": phone,
        "bank": bank,
        "bank_account": bank_account,
        "quantity": quantity,
        "amount": float(amount),
        "order": online_order_id or order_id,
    }
    business_fields: list[dict[str, Any]] = []
    seen_field_ids: set[str] = set()
    for field_key in (
        "invoice_type",
        "shop_name",
        "invoice_entity",
        "title",
        "tax_no",
        "address",
        "phone",
        "bank",
        "bank_account",
        "quantity",
        "amount",
        "order",
    ):
        field_id = str(fields.get(field_key) or "").strip()
        if not field_id or field_id in seen_field_ids:
            continue
        seen_field_ids.add(field_id)
        business_fields.append({"fieldId": field_id, "value": _merge_field_value(field_key, fields, values)})
    return {
        "uid": "",
        "coid": "",
        "ip": "",
        "page": {},
        "data": {
            "woTypeId": workorder_type_id,
            "title": WORKORDER_TITLE,
            "beginTime": begin_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "grade": DEFAULT_GRADE,
            "businessField": business_fields,
            "dingDingMsg": False,
            "woSource": DEFAULT_WORKORDER_SOURCE,
            "orderIds": [internal_order_id],
            "openUserId": "",
            "paymentType": None,
            "expressInterceptType": None,
            "remarkSoId": None,
        },
    }


def _load_template() -> dict[str, Any]:
    path = _template_path()
    if not path.exists():
        raise RuntimeError(f"未找到发票工单模板：{path}。请先运行 `ops jst order invoice learn`。")
    return _read_json(path)


def _write_template(
    scene_data: dict[str, Any] | None = None,
    *,
    workorder_type_id: str | None = None,
    field_map: dict[str, str] | None = None,
) -> Path:
    template = {
        "site": JST_SITE,
        "scene": JST_ORDER_INVOICE_SCENE,
        "capture_source": "sessionhub_9222",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "endpoint": BATCH_INSERT_URL,
        "method": "POST",
        "workorder_type_id": workorder_type_id or DEFAULT_WORKORDER_TYPE_ID,
        "workorder_title": WORKORDER_TITLE,
        "field_map": {**DEFAULT_FIELD_MAP, **(field_map or {})},
        "defaults": {
            "invoice_type": DEFAULT_INVOICE_TYPE,
            "quantity": DEFAULT_QUANTITY,
            "order_lookup_scenes": [JST_ORDER_SCENE],
            "order_lookup_keys": [OUTER_ORDER_FILTER_KEY, *ORDER_ID_FILTER_KEYS],
        },
        "captured_request": {
            "url": (scene_data or {}).get("url"),
            "method": (scene_data or {}).get("method"),
            "post_data_json": (scene_data or {}).get("post_data_json"),
        },
        "notes": [
            "learn 会优先通过聚水潭工单类型接口自动发现发票工单 woTypeId 和 fieldId。",
            "Cookie、Authorization、Token 不写入代码；运行时从 SessionHub 获取。",
        ],
    }
    path = _template_path()
    _write_json(path, template)
    return path


def _api_post(session: dict[str, Any], url: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    with build_client(follow_redirects=True, timeout=timeout) as client:
        response = client.post(url, headers=_api_headers(session), json=payload)
        response.raise_for_status()
        data = response.json()
    return data if isinstance(data, dict) else {"data": data}


def _is_success_response(data: dict[str, Any]) -> bool:
    if data.get("success") is False:
        return False
    code = data.get("code")
    if code in (0, "0", None):
        return True
    return bool(data.get("data"))


def _call_workorder_metadata_api(session: dict[str, Any], url: str, data: dict[str, Any]) -> dict[str, Any]:
    identity = _session_identity(session)
    last_response: dict[str, Any] | None = None
    for payload in (data, {**identity, "page": {}, "data": data}):
        response = _api_post(session, url, payload)
        if _is_success_response(response):
            return response
        last_response = response
    raise RuntimeError(f"聚水潭工单元数据接口失败：{url} -> {last_response}")


def _iter_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in ("list", "rows", "records", "data", "pageList"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def _find_invoice_workorder_type_id(data: dict[str, Any]) -> str:
    for record in _iter_records(data.get("data")):
        name = str(record.get("name") or record.get("woTypeName") or record.get("title") or "").strip()
        if "发票" not in name:
            continue
        workorder_type_id = str(record.get("id") or record.get("woTypeId") or "").strip()
        if workorder_type_id:
            return workorder_type_id
    raise RuntimeError("未在聚水潭工单类型列表中找到“发票”工单类型")


def _extract_field_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    detail = data.get("data")
    if isinstance(detail, list):
        return [item for item in detail if isinstance(item, dict)]
    if not isinstance(detail, dict):
        return []
    for key in ("fieldList", "fields", "businessFieldList", "woFieldList"):
        nested = detail.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def _match_field_id(field_list: list[dict[str, Any]], aliases: list[str]) -> str:
    for alias in aliases:
        for field in field_list:
            field_name = str(field.get("fieldName") or field.get("name") or field.get("label") or "").strip()
            if field_name != alias:
                continue
            field_id = str(field.get("fieldId") or field.get("id") or field.get("value") or "").strip()
            if field_id:
                return field_id
    return ""


def _build_field_map(field_list: list[dict[str, Any]]) -> dict[str, str]:
    field_map = dict(DEFAULT_FIELD_MAP)
    missing: list[str] = []
    for key, aliases in FIELD_NAME_ALIASES.items():
        field_id = _match_field_id(field_list, aliases)
        if field_id:
            field_map[key] = field_id
        elif key in {"invoice_type", "shop_name", "invoice_entity", "title", "amount", "order"}:
            missing.append("/".join(aliases))
    if missing:
        raise RuntimeError(f"发票工单字段定义缺少关键字段：{', '.join(missing)}")
    return field_map


def _discover_workorder_template_config() -> dict[str, Any]:
    session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
    type_list = _call_workorder_metadata_api(session, WORKORDER_TYPE_LIST_URL, {"pageIndex": 1, "pageSize": 100})
    workorder_type_id = _find_invoice_workorder_type_id(type_list)
    detail = _call_workorder_metadata_api(session, WORKORDER_TYPE_DETAIL_URL, {"woTypeId": workorder_type_id})
    field_list = _extract_field_list(detail)
    if not field_list:
        raise RuntimeError("聚水潭未返回发票工单字段定义")
    return {
        "workorder_type_id": workorder_type_id,
        "field_map": _build_field_map(field_list),
    }


def learn_order_invoice_workorder(*, force: bool = False) -> CommandResponse:
    manager = get_scene_manager()
    manager.ensure_scene(JST_SITE, JST_ORDER_SCENE)
    scene_data: dict[str, Any] | None = None
    try:
        scene_data = manager.capture_scene(JST_SITE, JST_ORDER_INVOICE_SCENE) if force else manager.ensure_scene(JST_SITE, JST_ORDER_INVOICE_SCENE)
    except Exception as exc:
        scene_data = {"capture_status": "template_seeded_without_scene", "reason": str(exc)}
    discovery_source = "workorder_metadata_api"
    discovery_error = ""
    try:
        discovered = _discover_workorder_template_config()
    except Exception as exc:
        discovery_source = "fallback_defaults"
        discovery_error = str(exc)
        discovered = {
            "workorder_type_id": DEFAULT_WORKORDER_TYPE_ID,
            "field_map": dict(DEFAULT_FIELD_MAP),
        }
    template_path = _write_template(
        scene_data,
        workorder_type_id=str(discovered.get("workorder_type_id") or "").strip() or DEFAULT_WORKORDER_TYPE_ID,
        field_map=discovered.get("field_map") if isinstance(discovered.get("field_map"), dict) else None,
    )
    context_path = write_runtime_context(
        task_name="jst_order_invoice_learn",
        status="success",
        inputs={"force": force},
        outputs={
            "site": JST_SITE,
            "scene": JST_ORDER_INVOICE_SCENE,
            "template_path": str(template_path),
            "scene_data_status": scene_data.get("status") if isinstance(scene_data, dict) else None,
            "workorder_type_id": discovered.get("workorder_type_id"),
            "discovery_source": discovery_source,
            "discovery_error": discovery_error,
        },
        artifacts=[str(template_path)],
    )
    return CommandResponse(
        success=True,
        platform="jst",
        command="order invoice learn",
        data={
            "site": JST_SITE,
            "scene": JST_ORDER_INVOICE_SCENE,
            "template_path": str(template_path),
            "context_path": str(context_path),
            "source": "sessionhub_9222",
            "workorder_type_id": discovered.get("workorder_type_id"),
            "discovery_source": discovery_source,
            "discovery_error": discovery_error,
            "next_command": "ops --json jst order invoice --order-id <订单号> --shop-name <店铺名称> --invoice-entity <开票主体公司> --title <抬头> --tax-no <税号> --address <地址> --phone <电话> --bank <开户行> --bank-account <账号> --amount <金额>",
        },
    )


def _post_workorder(payload: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
    endpoint = str(template.get("endpoint") or template.get("url") or BATCH_INSERT_URL)
    method = str(template.get("method") or "POST").upper()
    headers = _api_headers(session, template.get("headers") if isinstance(template.get("headers"), dict) else None)
    with build_client(follow_redirects=True, timeout=60.0) as client:
        response = client.request(method, endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(f"创建发票工单失败：{data}")
    if isinstance(data, dict) and data.get("code") not in (None, 0, "0"):
        raise RuntimeError(f"创建发票工单失败：{data}")
    return data if isinstance(data, dict) else {"data": data}


def run_order_invoice_workorder(
    *,
    order_id: str,
    outer_order_id: str | None,
    invoice_type: str,
    shop_name: str,
    invoice_entity: str,
    title: str,
    tax_no: str,
    address: str,
    phone: str,
    bank: str,
    bank_account: str,
    amount: str,
    quantity: int,
    execute: bool,
) -> CommandResponse:
    if not order_id and not outer_order_id:
        raise RuntimeError("请传入 --order-id 或 --outer-order-id")
    if quantity <= 0:
        raise RuntimeError("--quantity 必须大于 0")
    _validate_required(
        shop_name=shop_name,
        invoice_entity=invoice_entity,
        title=title,
        tax_no=tax_no,
        address=address,
        phone=phone,
        bank=bank,
        bank_account=bank_account,
    )
    amount_value = _money(amount)
    lookup_id = order_id or outer_order_id or ""
    identity = _resolve_order_identity(order_id=lookup_id, outer_order_id=outer_order_id)
    template = _load_template() if execute else {}
    field_map = template.get("field_map") if isinstance(template.get("field_map"), dict) else DEFAULT_FIELD_MAP
    workorder_type_id = str(template.get("workorder_type_id") or DEFAULT_WORKORDER_TYPE_ID)
    payload = build_invoice_workorder_payload(
        order_id=lookup_id,
        internal_order_id=identity["internal_order_id"],
        online_order_id=identity.get("online_order_id") or "",
        invoice_type=invoice_type or DEFAULT_INVOICE_TYPE,
        shop_name=shop_name,
        invoice_entity=invoice_entity,
        title=title,
        tax_no=tax_no,
        address=address,
        phone=phone,
        bank=bank,
        bank_account=bank_account,
        amount=amount_value,
        quantity=quantity,
        field_map=field_map,
        workorder_type_id=workorder_type_id,
    )
    result: dict[str, Any] | None = None
    if execute:
        result = _post_workorder(payload, template)
    outputs = {
        "order_id": lookup_id,
        "outer_order_id": outer_order_id,
        "matched_filter": identity.get("matched_filter"),
        "internal_order_id": identity["internal_order_id"],
        "online_order_id": identity.get("online_order_id"),
        "invoice_type": invoice_type or DEFAULT_INVOICE_TYPE,
        "shop_name": shop_name,
        "invoice_entity": invoice_entity,
        "title": title,
        "tax_no": tax_no,
        "amount": float(amount_value),
        "quantity": quantity,
        "submitted": execute,
        "mode": "execute" if execute else "dry-run",
        "scene": JST_ORDER_INVOICE_SCENE,
        "payload": payload,
        "result": result or {},
    }
    context_path = write_runtime_context(
        task_name="jst_order_invoice_run",
        status="success",
        inputs={
            "order_id": order_id,
            "outer_order_id": outer_order_id,
            "invoice_type": invoice_type,
            "shop_name": shop_name,
            "invoice_entity": invoice_entity,
            "title": title,
            "tax_no": tax_no,
            "amount": amount,
            "quantity": quantity,
            "execute": execute,
        },
        outputs=outputs,
    )
    return CommandResponse(
        success=True,
        platform="jst",
        command="order invoice",
        data={**outputs, "context_path": str(context_path)},
    )
