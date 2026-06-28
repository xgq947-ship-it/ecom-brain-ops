from __future__ import annotations

import base64
import hmac
import mimetypes
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import formatdate
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ops_cli.capabilities import mark_scene_refreshed
from ops_cli.capabilities import require_interactive_recovery
from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.output import CommandResponse
from ops_cli.platforms.auth_shared import is_probable_auth_error
from ops_cli.platforms.jst.order import DEFAULT_JST_ORDER_PATH
from ops_cli.platforms.jst.order import JST_ORDER_SCENE
from ops_cli.platforms.jst.order import JST_SITE
from ops_cli.platforms.jst.order import OUTER_ORDER_FILTER_KEY
from ops_cli.platforms.jst.order import _extract_form_template
from ops_cli.platforms.jst.order import _first_text
from ops_cli.platforms.jst.order import _query_order_rows
from ops_cli.platforms.jst.shops import default_shop, resolve_shop
from ops_cli.runtime_context import write_runtime_context
from ops_cli.utils.http import build_client


WORKORDER_TYPE_ID = "48DF537274074D87B7BBB8A7EEAD6B21"
WORKORDER_TITLE = "运营特殊单报销打款"
ORDER_FIELD_NAME = "LP线上订单号"
BANK_INFO = "6212261407005274259+肖国清"
SHOP_ID = default_shop().shop_id
WORKORDER_SOURCE = "PC_ORDER"
SPECIAL_ORDER_TEXT = "特殊单"
WORKORDER_ORIGIN = "https://shouhou.erp321.com"
WORKORDER_REFERER = "https://shouhou.erp321.com/"
DEFAULT_EXECUTOR_IDS = [17083890]
DEFAULT_COPY_SENDER_IDS = [13848164]
DEFAULT_GRADE = "NORMAL"
DEFAULT_ALIPAY_ACCOUNT = "15659384388"
DEFAULT_ALIPAY_NAME = "Evan"
SELECT_CREATED_URL = "https://api.erp321.com/jgd/api/gd/workOrder/selectCreatedWoByField"
STS_TOKEN_URL = "https://api.erp321.com/jgd/api/gd/aliyun/oss/getStsToken"
BATCH_INSERT_URL = "https://api.erp321.com/jgd/api/gd/workOrder/batchInsert"

FIELD_BANK_INFO = "TEXTField1023127320230801180658"
FIELD_PRINCIPAL = "NUMBERField1023127320230729214743"
FIELD_AMOUNT = "amountField"
FIELD_SHOP = "shopField"
FIELD_SKU = "skuIdField"
FIELD_ITEM_NAME = "iIdField"
FIELD_ORDER_TYPE = "SELECTField1023127320230728172433"
FIELD_FILE = "FILE_UPLOADField1023127320230728164848"


def _money(value: str | Decimal) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("金额必须是合法数字") from exc
    if amount < 0:
        raise RuntimeError("金额不能小于 0")
    return amount.quantize(Decimal("0.01"))


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


def _api_headers(session: dict[str, Any]) -> dict[str, str]:
    source_headers = {str(key): str(value) for key, value in (session.get("headers") or {}).items()}
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": WORKORDER_ORIGIN,
        "Referer": WORKORDER_REFERER,
    }
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
        raise RuntimeError("无法从当前 Session 提取 u_id / u_co_id，请重新捕获聚水潭订单列表请求。")
    return {"uid": uid, "coid": coid, "ip": ""}


def _online_order_id(row: dict[str, Any]) -> str:
    for key in ("so_id", "raw_so_id", "pre_so_id"):
        value = str(row.get(key) or "").strip()
        if value.startswith("LP"):
            return value
    return _first_text(row, ("so_id", "raw_so_id", "pre_so_id"))


def _item_name(row: dict[str, Any], fallback: str) -> str:
    items = row.get("items")
    if isinstance(items, list) and items:
        first = items[0] if isinstance(items[0], dict) else {}
        name = str(first.get("name") or "").strip()
        if name:
            return name
    return _first_text(row, ("skus", "name", "item_name", "i_name")) or fallback


def _resolve_order_identity(outer_order_id: str) -> dict[str, str]:
    session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
    headers = dict(session.get("headers") or {})
    cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
    if not cookie:
        raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")
    url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
    form_template = _extract_form_template(session)

    with build_client(follow_redirects=True, timeout=60.0) as client:
        rows = _query_order_rows(client, url, cookie, OUTER_ORDER_FILTER_KEY, outer_order_id, form_template)
    if not rows:
        raise RuntimeError("聚水潭未找到指定订单")
    if len(rows) > 1:
        raise RuntimeError(f"聚水潭返回 {len(rows)} 条订单，请换更精确的订单号")
    row = rows[0]
    internal_order_id = str(row.get("o_id") or "").strip()
    online_order_id = _online_order_id(row)
    if not internal_order_id or not online_order_id:
        raise RuntimeError("聚水潭订单缺少 o_id 或 LP线上订单号，无法创建工单")
    return {
        "outer_order_id": outer_order_id,
        "matched_filter": OUTER_ORDER_FILTER_KEY,
        "internal_order_id": internal_order_id,
        "online_order_id": online_order_id,
        "item_name": _item_name(row, ""),
    }


def _api_post(session: dict[str, Any], url: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
    with build_client(follow_redirects=True, timeout=timeout) as client:
        response = client.post(url, headers=_api_headers(session), json=payload)
        response.raise_for_status()
        data = response.json()
    if isinstance(data, dict) and (data.get("success") is not True or data.get("code") != 0):
        raise RuntimeError(f"接口调用失败：{url} -> {data}")
    return data if isinstance(data, dict) else {"data": data}


def _select_created_workorder(session: dict[str, Any], identity: dict[str, str], online_order_id: str, internal_order_id: str, outer_order_id: str) -> dict[str, Any]:
    payload = {
        **identity,
        "page": {},
        "data": {
            "orderField": ORDER_FIELD_NAME,
            "createByOrder": True,
            "openUserId": "",
            "woTypeId": WORKORDER_TYPE_ID,
            "orderValue": online_order_id,
            "onlineOrder": online_order_id,
            "internalOrder": internal_order_id,
            "outerSoId": outer_order_id,
        },
    }
    response = _api_post(session, SELECT_CREATED_URL, payload)
    return response.get("data") or {}


def has_existing_workorder(result: dict[str, Any]) -> bool:
    return any(str(result.get(key) or "").strip() for key in ("onlineOrder", "internalOrder", "buyerAccount", "logisticsNumber"))


def _get_sts_token(session: dict[str, Any], identity: dict[str, str]) -> dict[str, Any]:
    response = _api_post(session, STS_TOKEN_URL, {**identity, "page": {}})
    data = response.get("data") or {}
    required = ["region", "accessKeyId", "accessKeySecret", "stsToken", "bucketName", "dir", "ossUrlDomain"]
    missing = [name for name in required if not data.get(name)]
    if missing:
        raise RuntimeError(f"STS 返回缺少字段：{', '.join(missing)}")
    return data


def _upload_workbook_to_oss(sts: dict[str, Any], workbook_path: Path) -> str:
    content_type = mimetypes.guess_type(workbook_path.name)[0] or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    region = str(sts["region"]).strip()
    bucket_name = str(sts["bucketName"]).strip()
    object_key = f"{str(sts['dir']).rstrip('/')}/{workbook_path.name}"
    endpoint = f"https://{bucket_name}.oss-{region}.aliyuncs.com/{quote(object_key)}"
    date_header = formatdate(usegmt=True)
    security_token = str(sts["stsToken"]).strip()
    canonical_headers = f"x-oss-security-token:{security_token}"
    canonical_resource = f"/{bucket_name}/{object_key}"
    string_to_sign = f"PUT\n\n{content_type}\n{date_header}\n{canonical_headers}\n{canonical_resource}"
    signature = base64.b64encode(
        hmac.new(str(sts["accessKeySecret"]).encode("utf-8"), string_to_sign.encode("utf-8"), sha1).digest()
    ).decode("utf-8")
    headers = {
        "Date": date_header,
        "Content-Type": content_type,
        "Authorization": f"OSS {sts['accessKeyId']}:{signature}",
        "x-oss-security-token": security_token,
    }
    with build_client(follow_redirects=True, timeout=60.0) as client:
        response = client.put(endpoint, headers=headers, content=workbook_path.read_bytes())
        response.raise_for_status()
    if response.status_code not in (200, 201):
        raise RuntimeError(f"OSS 上传失败：{response.status_code} {response.text[:200]}")
    return str(sts["ossUrlDomain"]).rstrip("/") + "/" + object_key


def build_reimburse_workorder_payload(
    *,
    internal_order_id: str,
    online_order_id: str,
    principal_total: Decimal,
    payout_total: Decimal,
    product_code: str,
    item_name: str,
    upload_url: str,
    shop_id: str = SHOP_ID,
) -> dict[str, Any]:
    begin_time = datetime.now().replace(microsecond=0)
    end_time = begin_time + timedelta(days=3)
    return {
        "uid": "",
        "coid": "",
        "ip": "",
        "page": {},
        "data": {
            "woTypeId": WORKORDER_TYPE_ID,
            "title": WORKORDER_TITLE,
            "executorIds": DEFAULT_EXECUTOR_IDS,
            "copySenderIds": DEFAULT_COPY_SENDER_IDS,
            "beginTime": begin_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "grade": DEFAULT_GRADE,
            "partnerOrderStatus": None,
            "businessField": [
                {"fieldId": "alipayAccountField", "value": DEFAULT_ALIPAY_ACCOUNT},
                {"fieldId": "alipayNameField", "value": DEFAULT_ALIPAY_NAME},
                {"fieldId": "orderField", "value": online_order_id},
                {"fieldId": FIELD_BANK_INFO, "value": BANK_INFO},
                {"fieldId": FIELD_PRINCIPAL, "value": float(principal_total)},
                {"fieldId": FIELD_AMOUNT, "value": float(payout_total)},
                {"fieldId": FIELD_SHOP, "value": int(shop_id)},
                {"fieldId": FIELD_SKU, "value": [product_code]},
                {"fieldId": FIELD_ITEM_NAME, "value": [item_name or product_code]},
                {"fieldId": FIELD_ORDER_TYPE, "value": SPECIAL_ORDER_TEXT},
                {"fieldId": FIELD_FILE, "value": [upload_url]},
                {"fieldId": "NUMBERField1023127320231030173546"},
            ],
            "dingDingMsg": False,
            "woSource": WORKORDER_SOURCE,
            "orderIds": [internal_order_id],
            "openUserId": "",
            "paymentType": None,
            "expressInterceptType": None,
            "remarkSoId": None,
        },
    }


def _post_workorder(session: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    response = _api_post(session, BATCH_INSERT_URL, payload)
    if response.get("data") is not True:
        raise RuntimeError(f"创建工单返回异常：{response}")
    return response


def run_order_reimburse_workorder(
    *,
    outer_order_id: str,
    principal_total: str,
    payout_total: str,
    product_code: str,
    product_name: str,
    workbook_file: str,
    execute: bool,
    shop: str | None = None,
) -> CommandResponse:
    reimburse_shop_id = resolve_shop(shop).shop_id
    if not outer_order_id:
        raise RuntimeError("请传入 --outer-order-id")
    if not product_code:
        raise RuntimeError("请传入 --product-code")
    workbook_path = Path(workbook_file).expanduser().resolve()
    if execute and not workbook_path.exists():
        raise RuntimeError(f"登记表文件不存在：{workbook_path}")

    principal = _money(principal_total)
    payout = _money(payout_total)
    retried_for_auth = False
    auth_refresh_applied = False
    while True:
        try:
            session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
            identity = _session_identity(session)
            order_identity = _resolve_order_identity(outer_order_id)
            existing_detail = _select_created_workorder(
                session,
                identity,
                order_identity["online_order_id"],
                order_identity["internal_order_id"],
                outer_order_id,
            )
            existing = has_existing_workorder(existing_detail)
            upload_url = ""
            payload: dict[str, Any] | None = None
            result: dict[str, Any] | None = None
            submitted = False
            if execute and not existing:
                sts = _get_sts_token(session, identity)
                upload_url = _upload_workbook_to_oss(sts, workbook_path)
                payload = build_reimburse_workorder_payload(
                    internal_order_id=order_identity["internal_order_id"],
                    online_order_id=order_identity["online_order_id"],
                    principal_total=principal,
                    payout_total=payout,
                    product_code=product_code,
                    item_name=order_identity.get("item_name") or product_name or product_code,
                    upload_url=upload_url,
                    shop_id=reimburse_shop_id,
                )
                result = _post_workorder(session, payload)
                submitted = True
            break
        except Exception as exc:
            if not retried_for_auth and is_probable_auth_error(exc):
                require_interactive_recovery(JST_ORDER_SCENE)
                get_scene_manager().capture_scene(JST_SITE, JST_ORDER_SCENE)
                mark_scene_refreshed(JST_ORDER_SCENE)
                retried_for_auth = True
                auth_refresh_applied = True
                continue
            raise

    outputs = {
        "outer_order_id": outer_order_id,
        "matched_filter": order_identity.get("matched_filter"),
        "internal_order_id": order_identity["internal_order_id"],
        "online_order_id": order_identity["online_order_id"],
        "item_name": order_identity.get("item_name") or product_name,
        "has_existing_workorder": existing,
        "existing_detail": existing_detail,
        "principal_total": float(principal),
        "payout_total": float(payout),
        "product_code": product_code,
        "product_name": product_name,
        "workbook_file": str(workbook_path),
        "upload_url": upload_url,
        "submitted": submitted,
        "mode": "execute" if execute else "dry-run",
        "payload": payload or {},
        "result": result or {},
    }
    if auth_refresh_applied:
        outputs["auth_refresh_applied"] = True
    context_path = write_runtime_context(
        task_name="jst_order_reimburse_run",
        status="success",
        inputs={
            "outer_order_id": outer_order_id,
            "principal_total": principal_total,
            "payout_total": payout_total,
            "product_code": product_code,
            "product_name": product_name,
            "workbook_file": str(workbook_path),
            "execute": execute,
        },
        outputs=outputs,
    )
    return CommandResponse(
        success=True,
        platform="jst",
        command="order reimburse",
        data={**outputs, "context_path": str(context_path)},
    )
