"""聚水潭订单换货 / 补发能力。

边界：本模块是「平台层」，独占聚水潭页面探索、9222 Chrome、Selector、Cookie、URL、Playwright。
业务层（运营自动化工具/workflows）只通过 `ops --json jst order exchange-resend …` 调用，
拿到统一 JSON，不感知任何页面细节。

三个动作：
- learn   ：用 9222 Chrome 探索「售后 / 换货 / 补发」入口，截图 + 记录候选步骤，绝不提交。
            换货 learn 会在订单列表商品行打开「请选择换货商品」，清空默认款式编码、
            按商品编码搜索并选中换入目标；不点击「确定」，不触发 ChangeBatchItem。
- preview ：复用订单查询能力解析订单、判断是否允许换货 / 补发，输出 final_payload，绝不提交。
- submit  ：仅 --execute 且 confirm_order_no 一致时进入；先输出 final_payload。补发原订单商品
            使用已确认模板 / JTable1 的 CreateReissueOrderAllItem 官方调用；换货使用订单列表
            JTable1 / ChangeBatchItem 接口提交，不依赖浏览器点击「确定」。

安全红线（与业务层 README 一致）：
- 不修改订单金额、收货地址、账号密码、cookie、token、session。
- 不绕过验证码。
- 不无限重试。
- 真实提交前必须输出 final_payload。
- 找不到订单 / 状态不允许 / 商品不匹配 → eligible=False，调用方据此停止。
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ops_cli.browser import managed_work_page
from ops_cli.capabilities import mark_scene_refreshed
from ops_cli.capabilities import require_interactive_recovery
from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.output import CommandResponse
from ops_cli.platforms.auth_shared import is_probable_auth_error
from ops_cli.platforms.jst.order import (
    DEFAULT_JST_ORDER_PATH,
    JST_ORDER_SCENE,
    JST_SITE,
    _base_order_url,
    _build_request_form,
    _extract_form_template,
    _extract_json_payload,
    _first_text,
    _normalize_order_items,
    _query_order_rows_by_identifier,
)
from ops_cli.platforms.jst.shared import surface_jst_login_if_needed
from ops_cli.runtime_context import write_runtime_context
from ops_cli.utils.http import build_client


EXCHANGE_RESEND_SCENE = "order_exchange_resend"
CONFIRMED_TEMPLATE_PATH = Path("data/jst/order_exchange_resend_template.json")
MODE_LABELS = {"resend": "补发", "exchange": "换货"}
DEFAULT_EXCHANGE_ELIGIBLE_STATUS = ("已发货", "未发货", "已付款待审核", "异常")
# 订单状态出现以下关键字 → 不允许发起换货 / 补发
BLOCKED_STATUS_KEYWORDS = ("已取消", "作废", "草稿", "已关闭", "已退款", "退款中")
SENSITIVE_TEMPLATE_HEADERS = {"authorization", "cookie", "host", "content-length", "u_sso_token"}
# 提交渲染处出现这些占位符 → 模板按 SKU / 数量做部分操作（否则视为整单补发）
_ITEM_SELECTION_PLACEHOLDERS = ("__SKU__", "__EXCHANGE_SKU__", "__QTY__")
# 售后 / 换货 / 补发入口候选文案（探索用，非真实点击提交）
AFTERSALE_ENTRY_KEYWORDS = (
    "售后",
    "新建售后",
    "申请售后",
    "售后单",
    "换货",
    "补发",
    "退换货",
    "补寄",
)
ORDER_STATUS_KEYS = ("status", "order_status", "buyer_status")


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized not in MODE_LABELS:
        raise RuntimeError("--mode 仅支持 resend（补发）或 exchange（换货）")
    return normalized


def _sessionhub_root() -> Path:
    return Path(get_scene_manager().root)


def _confirmed_template() -> dict[str, Any] | None:
    """读取「已学习并人工确认」的换货 / 补发页面模板。

    只有当模板存在且 confirmed=true 时，submit 才被允许真正驱动页面提交。
    模板可用模式由 supported_modes 限定，避免换货误复用补发模板。
    """
    path = Path.cwd() / CONFIRMED_TEMPLATE_PATH
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(payload, dict) and payload.get("confirmed") is True:
        return payload
    return None


def _eligible_statuses_from_template(template: dict[str, Any] | None) -> tuple[str, ...]:
    statuses = (template or {}).get("eligible_status") or []
    if not isinstance(statuses, list):
        return ()
    return tuple(str(item).strip() for item in statuses if str(item).strip())


def _eligible_statuses_for_mode(template: dict[str, Any] | None, mode: str) -> tuple[str, ...]:
    statuses = list(_eligible_statuses_from_template(template))
    if mode == "exchange":
        statuses.extend(DEFAULT_EXCHANGE_ELIGIBLE_STATUS)
    return tuple(dict.fromkeys(item for item in statuses if item))


def _template_supports_mode(template: dict[str, Any] | None, mode: str) -> bool:
    if template is None:
        return False
    supported_modes = template.get("supported_modes")
    if not isinstance(supported_modes, list) or not supported_modes:
        return True
    allowed = [str(item).strip() for item in supported_modes if str(item).strip()]
    return mode in allowed


def _template_uses_item_selection(template: dict[str, Any] | None) -> bool:
    """模板是否按 SKU / 数量做部分操作（提交渲染处引用了 SKU/QTY 占位符）。

    用于区分「整单补发」(只用 __O_ID__) 与「按 SKU 部分操作」模板：
    前者会忽略 --sku-code / --qty，后者会真正用到。
    """
    if not template:
        return False
    probe = {
        "args": (template.get("jtable_call") or {}).get("args_template"),
        "post": template.get("post_data_template")
        or template.get("post_data_json_template")
        or template.get("post_data"),
    }
    blob = json.dumps(probe, ensure_ascii=False, default=str)
    return any(placeholder in blob for placeholder in _ITEM_SELECTION_PLACEHOLDERS)


def _partial_resend_conflict(
    mode: str, template: dict[str, Any] | None, sku_code: str | None, qty: int | None
) -> str | None:
    """整单补发模板下，显式传 --sku-code / --qty 会误导（实际整单补发），返回拦截原因。

    仅在「确认模板支持 resend 且为整单补发（不引用 SKU/QTY 占位符）」时生效；
    无模板或按 SKU 模板时返回 None，不影响其它路径。
    """
    if mode != "resend":
        return None
    if template is None or not _template_supports_mode(template, mode):
        return None
    if _template_uses_item_selection(template):
        return None
    problems: list[str] = []
    if sku_code:
        problems.append(f"--sku-code（{sku_code}）")
    if qty is not None and int(qty) != 1:
        problems.append(f"--qty（{qty}）")
    if not problems:
        return None
    return (
        "当前补发为整单补发（按原订单全部明细原样补发），不支持按 "
        + " / ".join(problems)
        + " 做部分补发；请去掉这些参数后重试。"
    )


def _load_order_session() -> tuple[dict[str, Any], str, str, dict[str, str]]:
    session = get_scene_manager().ensure_scene(JST_SITE, JST_ORDER_SCENE)
    headers = dict(session.get("headers") or {})
    cookie = str(headers.get("Cookie") or headers.get("cookie") or "").strip()
    if not cookie:
        raise RuntimeError("SessionHub 已返回 session，但缺少 Cookie。请重新捕获聚水潭会话。")
    url = str(session.get("url") or f"https://www.erp321.com{DEFAULT_JST_ORDER_PATH}").strip()
    form_template = _extract_form_template(session)
    return session, cookie, url, form_template


def _status_matches_eligible(status: str, eligible_status: tuple[str, ...]) -> bool:
    return any(item == status or item in status for item in eligible_status)


def _select_order_row(rows: list[dict[str, Any]], eligible_status: tuple[str, ...] = ()) -> dict[str, Any] | None:
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    if eligible_status:
        eligible_rows = [
            row
            for row in rows
            if _status_matches_eligible(_first_text(row, ORDER_STATUS_KEYS), eligible_status)
        ]
        if len(eligible_rows) == 1:
            return eligible_rows[0]
    return None


def _resolve_order(order_no: str, *, eligible_status: tuple[str, ...] = ()) -> dict[str, Any]:
    """复用订单查询能力按订单号定位单条订单。"""
    _session, cookie, url, form_template = _load_order_session()
    with build_client(follow_redirects=True, timeout=60.0) as client:
        rows, filter_key = _query_order_rows_by_identifier(
            client,
            url,
            cookie,
            order_id=order_no,
            outer_order_id=None,
            identifier=order_no,
            form_template=form_template,
        )
    if not rows:
        return {"found_order": False, "matched_filter": filter_key}
    row = _select_order_row(rows, eligible_status=eligible_status)
    if row is None:
        raise RuntimeError(f"聚水潭返回 {len(rows)} 条订单，请换更精确的订单号")
    return {
        "found_order": True,
        "matched_filter": filter_key,
        "matched_count": len(rows),
        "internal_order_id": str(row.get("o_id") or "").strip(),
        "online_order_id": _first_text(row, ("so_id", "raw_so_id", "pre_so_id")),
        "order_status": _first_text(row, ORDER_STATUS_KEYS),
        "shop_name": _first_text(row, ("shop_name", "store_name", "shop")),
        "items": _normalize_order_items(row),
        "raw_items": row.get("items") if isinstance(row.get("items"), list) else [],
        "co_id": row.get("co_id"),
        "wms_co_id": row.get("wms_co_id"),
        "raw_order": row,
    }


def _evaluate_eligibility(
    resolved: dict[str, Any], *, mode: str, sku_code: str | None, eligible_status: tuple[str, ...] = ()
) -> tuple[bool, str | None, bool | None]:
    """返回 (eligible, ineligible_reason, sku_matched)。"""
    label = MODE_LABELS[mode]
    if not resolved.get("found_order"):
        return False, "聚水潭未找到该订单", None
    status = str(resolved.get("order_status") or "")
    if eligible_status:
        if not _status_matches_eligible(status, eligible_status):
            return False, f"订单状态「{status}」不在已确认允许{label}状态：{list(eligible_status)}", None
    else:
        for keyword in BLOCKED_STATUS_KEYWORDS:
            if keyword in status:
                return False, f"订单状态「{status}」不允许{label}", None
    sku_matched: bool | None = None
    if sku_code and mode != "exchange":
        codes = [str(item.get("product_code") or "").strip() for item in resolved.get("items") or []]
        sku_matched = sku_code in codes
        if not sku_matched:
            return False, f"指定商品编码 {sku_code} 不在订单商品中：{codes}", sku_matched
    return True, None, sku_matched


def _build_final_payload(
    resolved: dict[str, Any],
    *,
    mode: str,
    reason: str | None,
    remark: str | None,
    sku_code: str | None,
    qty: int,
) -> dict[str, Any]:
    items = resolved.get("items") or []
    first_sku = None
    if items:
        first_item = items[0] if isinstance(items[0], dict) else {}
        first_sku = str(first_item.get("product_code") or "").strip() or None
    exchange_sku_code = None
    if mode == "exchange":
        exchange_sku_code = sku_code or first_sku
        sku_code = first_sku
    elif not sku_code:
        sku_code = first_sku
    return {
        "action": mode,
        "mode_label": MODE_LABELS[mode],
        "internal_order_id": resolved.get("internal_order_id"),
        "online_order_id": resolved.get("online_order_id"),
        "order_status": resolved.get("order_status"),
        "shop_name": resolved.get("shop_name"),
        "sku_code": sku_code or None,
        "exchange_sku_code": exchange_sku_code or None,
        "qty": int(qty),
        "reason": reason or "",
        "remark": remark or "",
        "items": items,
    }


def _base_data(order_no: str, mode: str, action: str) -> dict[str, Any]:
    return {
        "order_no": order_no,
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "action": action,
        "found_order": False,
        "order_status": None,
        "eligible": False,
        "ineligible_reason": None,
        "sku_matched": None,
        "steps_detected": [],
        "final_payload": {},
        "submitted": False,
        "pending_confirmation": [],
        "screenshot_paths": [],
        "source": "order_list",
    }


def _extract_cookie_value(cookie: str, name: str) -> str:
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == name:
            return value.strip()
    return ""


def _placeholder_values(final_payload: dict[str, Any], order_no: str) -> dict[str, str]:
    sku = str(final_payload.get("sku_code") or final_payload.get("exchange_sku_code") or "")
    return {
        "__O_ID__": str(final_payload.get("internal_order_id") or ""),
        "__INTERNAL_ORDER_ID__": str(final_payload.get("internal_order_id") or ""),
        "__ONLINE_ORDER_ID__": str(final_payload.get("online_order_id") or ""),
        "__ORDER_NO__": str(order_no or final_payload.get("online_order_id") or ""),
        "__MODE__": str(final_payload.get("action") or ""),
        "__MODE_LABEL__": str(final_payload.get("mode_label") or ""),
        "__SKU__": sku,
        "__EXCHANGE_SKU__": str(final_payload.get("exchange_sku_code") or sku),
        "__QTY__": str(final_payload.get("qty") or ""),
        "__REASON__": str(final_payload.get("reason") or ""),
        "__REMARK__": str(final_payload.get("remark") or ""),
        "__SHOP_NAME__": str(final_payload.get("shop_name") or ""),
        "__ORDER_STATUS__": str(final_payload.get("order_status") or ""),
        "__EXCHANGE_ITEMS_JSON__": str(final_payload.get("exchange_items_json") or ""),
        "__KEEP_TARGET_INFO__": str(final_payload.get("keep_target_info") or "false").lower(),
    }


def _render_template_value(value: Any, placeholders: dict[str, str]) -> Any:
    if isinstance(value, str):
        rendered = value
        for placeholder, replacement in placeholders.items():
            rendered = rendered.replace(placeholder, replacement)
        return rendered
    if isinstance(value, list):
        return [_render_template_value(item, placeholders) for item in value]
    if isinstance(value, dict):
        return {key: _render_template_value(item, placeholders) for key, item in value.items()}
    return value


def _exchange_candidate_steps() -> list[dict[str, Any]]:
    return [
        {
            "stage": "order_list",
            "action": "search_order",
            "field": "outer_so_id",
        },
        {
            "stage": "order_list",
            "action": "open_item_detail",
            "source": "matched_order_row",
        },
        {
            "stage": "order_item_row",
            "action": "open_exchange_picker",
            "trigger": "row_exchange_button",
        },
        {
            "stage": "exchange_picker",
            "action": "search_target_sku",
            "field": "sku_code",
            "sku_placeholder": "__EXCHANGE_SKU__",
        },
        {
            "stage": "exchange_picker",
            "action": "select_target_sku",
            "sku_placeholder": "__EXCHANGE_SKU__",
        },
        {
            "stage": "exchange_picker",
            "action": "confirm_exchange_picker",
            "method": "ChangeBatchItem",
            "called": False,
        },
        {
            "stage": "order_list",
            "action": "submit_method_detected",
            "method": "ChangeBatchItem",
            "called": False,
        },
    ]


def _build_exchange_submit_candidate(
    final_payload: dict[str, Any],
    *,
    request_data_preview: dict[str, Any] | None = None,
    rows_preview: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """生成换货真实提交路径元数据。"""
    sku = str(final_payload.get("sku_code") or "").strip()
    exchange_sku = str(final_payload.get("exchange_sku_code") or sku).strip()
    qty = int(final_payload.get("qty") or 1)
    request_preview = request_data_preview or {
        "type": "换货",
        "question_type": str(final_payload.get("reason") or "质量问题"),
        "refund": "0",
        "exchangeamount": "0",
        "remark": str(final_payload.get("remark") or ""),
    }
    rows = rows_preview or [
        {
            "type": "退货",
            "sku_id": "__SKU__",
            "qty": "__QTY__",
            "source": "original_order_item",
        },
        {
            "type": "换货",
            "sku_id": "__EXCHANGE_SKU__",
            "qty": "__QTY__",
            "source": "goods_selector",
        },
    ]
    return {
        "confirmed": True,
        "dry_run_only": False,
        "request_kind": "jtable_call",
        "supported_modes": ["exchange"],
        "method": "POST",
        "url": "https://www.erp321.com/app/order/order/list.aspx",
        "submit_method": "ChangeBatchItem",
        "jtable_call": {
            "callback_id": "JTable1",
            "method": "ChangeBatchItem",
            "args_template": ["__O_ID__", "__EXCHANGE_ITEMS_JSON__", "__KEEP_TARGET_INFO__"],
            "call_control": "{page}",
        },
        "flow": _exchange_candidate_steps(),
        "field_map": {
            "internal_order_id": "__O_ID__",
            "return_sku_code": "__SKU__",
            "exchange_sku_code": "__EXCHANGE_SKU__",
            "exchange_items_json": "__EXCHANGE_ITEMS_JSON__",
            "keep_target_info": "__KEEP_TARGET_INFO__",
            "qty": "__QTY__",
            "question_type": "__REASON__",
            "remark": "__REMARK__",
        },
        "eligible_status": list(DEFAULT_EXCHANGE_ELIGIBLE_STATUS),
        "steps_detected": _exchange_candidate_steps(),
        "request_data_preview": request_preview,
        "rows_preview": rows,
        "defaults": {
            "return_sku_code": sku,
            "exchange_sku_code": exchange_sku,
            "qty": qty,
        },
        "submit_enabled_reason": "Ops-Cli 已固化订单列表 JTable1 / ChangeBatchItem 接口；submit --mode exchange --execute 直接调用接口提交。",
    }


def _submit_headers(template: dict[str, Any], session: dict[str, Any], cookie: str, url: str) -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie,
    }
    for key, value in (template.get("headers") or {}).items():
        key_text = str(key)
        if key_text.lower() in SENSITIVE_TEMPLATE_HEADERS:
            continue
        if value is not None:
            headers[key_text] = str(value)
    source_headers = {str(key): str(value) for key, value in (session.get("headers") or {}).items()}
    for source_key in ("User-Agent", "user-agent", "X-Requested-With", "x-requested-with"):
        value = source_headers.get(source_key)
        if value:
            canonical = "-".join(part.capitalize() for part in source_key.split("-"))
            headers.setdefault(canonical, value)
    u_sso_token = _extract_cookie_value(cookie, "u_sso_token")
    if u_sso_token:
        headers["u_sso_token"] = u_sso_token
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        headers.setdefault("Origin", f"{parsed.scheme}://{parsed.netloc}")
        headers.setdefault("Referer", f"{parsed.scheme}://{parsed.netloc}/")
    return headers


def _response_payload(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        return {"text": str(getattr(response, "text", ""))[:1000]}
    return payload if isinstance(payload, dict) else {"data": payload}


def _is_success_payload(payload: dict[str, Any]) -> bool:
    if payload.get("success") is False or payload.get("isSuccess") is False:
        return False
    if "code" in payload and str(payload.get("code")).strip().lower() not in {"0", "200", "success"}:
        return False
    if "errorCode" in payload and str(payload.get("errorCode")).strip().lower() not in {"0", "200", "success"}:
        return False
    if payload.get("result") is False:
        return False
    return True


def _jtable_result_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"return_value": payload}
    exception = str(payload.get("ExceptionMessage") or "").strip()
    message = str(payload.get("Message") or "").strip()
    if exception:
        raise RuntimeError(f"换货 / 补发提交失败：{exception}")
    if payload.get("GotoLogin") is True:
        raise RuntimeError("聚水潭登录已失效，请重新沉淀 SessionHub 会话")
    if payload.get("IsSuccess") is False:
        raise RuntimeError(f"换货 / 补发提交失败：{payload}")
    return_value = payload.get("ReturnValue")
    combined = f"{message} {return_value or ''}"
    if "短信" in combined or "验证码" in combined or "910001" in combined:
        raise RuntimeError(f"换货 / 补发提交需要人工验证：{combined.strip()}")
    return_text = str(return_value or "")
    match = re.search(r"创建成功\s*(\d+)\s*条[，,]\s*失败\s*(\d+)\s*条", return_text)
    if match and "提示:" not in return_text:
        success_count = int(match.group(1))
        failed_count = int(match.group(2))
        if failed_count > 0 or success_count <= 0:
            raise RuntimeError(f"换货 / 补发提交失败：{return_text.strip()}")
    return {
        "is_success": payload.get("IsSuccess"),
        "message": message,
        "return_value": return_value,
        "raw": payload,
    }


def _submit_jtable_call(
    *,
    template: dict[str, Any],
    final_payload: dict[str, Any],
    order_no: str,
) -> dict[str, Any]:
    call_template = template.get("jtable_call") or {}
    method_name = str(call_template.get("method") or "").strip()
    if not method_name:
        raise RuntimeError("confirmed 模板缺少 jtable_call.method")

    placeholders = _placeholder_values(final_payload, order_no)
    args = _render_template_value(call_template.get("args_template") or [], placeholders)
    if not isinstance(args, list):
        raise RuntimeError("confirmed 模板 jtable_call.args_template 必须是数组")

    call_control = str(call_template.get("call_control") or "{page}")
    callback_id = str(call_template.get("callback_id") or "JTable1")
    session, cookie, order_url, form_template = _load_order_session()
    url = _base_order_url(str(template.get("url") or order_url))
    callback_param = {"Method": method_name, "Args": [str(item) for item in args], "CallControl": call_control}
    form = _build_request_form(method_name, callback_param, form_template=form_template)
    form["__CALLBACKID"] = callback_id
    headers = _submit_headers(template, session, cookie, url)
    headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    headers.setdefault("X-Requested-With", "XMLHttpRequest")
    headers.setdefault("Referer", url)

    attempts: list[dict[str, Any]] = []
    with build_client(follow_redirects=True, timeout=60.0) as client:
        response = client.post(url, headers=headers, data=form)
        response.raise_for_status()
        payload = _extract_json_payload(response.text)
        result = _jtable_result_payload(payload)
        attempts.append(result)

        return_value = str(result.get("return_value") or "")
        force_index = int(call_template.get("force_arg_index", 1))
        if (
            "提示:" in return_value
            and call_template.get("force_retry_on_prompt") is True
            and 0 <= force_index < len(callback_param["Args"])
        ):
            callback_param["Args"][force_index] = "true"
            form = _build_request_form(method_name, callback_param, form_template=form_template)
            form["__CALLBACKID"] = callback_id
            response = client.post(url, headers=headers, data=form)
            response.raise_for_status()
            payload = _extract_json_payload(response.text)
            result = _jtable_result_payload(payload)
            attempts.append(result)
        elif "提示:" in return_value:
            raise RuntimeError(f"换货 / 补发提交返回二次确认提示，未自动确认：{return_value}")

    result = dict(attempts[-1])
    if "提示:" in str(result.get("return_value") or ""):
        raise RuntimeError(f"换货 / 补发提交返回二次确认提示：{result['return_value']}")
    result["attempts"] = attempts
    return result


def _submit_from_template(
    *,
    template: dict[str, Any],
    final_payload: dict[str, Any],
    order_no: str,
) -> dict[str, Any]:
    mode = str(final_payload.get("action") or "").strip()
    if not _template_supports_mode(template, mode):
        allowed = [str(item).strip() for item in template.get("supported_modes", []) if str(item).strip()]
        raise RuntimeError(f"confirmed 模板不支持 {mode}，允许模式：{allowed}")

    if str(template.get("request_kind") or "").strip() == "jtable_call":
        return _submit_jtable_call(template=template, final_payload=final_payload, order_no=order_no)

    url = str(template.get("url") or "").strip()
    if not url:
        raise RuntimeError("confirmed 模板缺少 url")
    method = str(template.get("method") or "POST").strip().upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise RuntimeError(f"confirmed 模板 method 不支持：{method}")

    body_template = template.get("post_data_template")
    if body_template is None:
        body_template = template.get("post_data_json_template", template.get("post_data"))
    rendered_body = _render_template_value(body_template, _placeholder_values(final_payload, order_no))
    session, cookie, _order_url, _form_template = _load_order_session()
    headers = _submit_headers(template, session, cookie, url)
    if isinstance(rendered_body, (dict, list)):
        headers.setdefault("Content-Type", "application/json;charset=UTF-8")
        request_kwargs = {"json": rendered_body}
    elif rendered_body is None:
        request_kwargs = {}
    else:
        request_kwargs = {"content": str(rendered_body)}

    with build_client(follow_redirects=True, timeout=60.0) as client:
        response = client.request(method, url, headers=headers, **request_kwargs)
        response.raise_for_status()
    payload = _response_payload(response)
    if not _is_success_payload(payload):
        raise RuntimeError(f"换货 / 补发提交失败：{payload}")
    return payload


def _to_int(value: Any, *, default: int = 0) -> int:
    try:
        text = str(value).strip()
        if not text:
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, *, default: float = 0.0) -> float:
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def _item_sku_candidates(item: dict[str, Any]) -> set[str]:
    keys = ("sku_id", "product_code", "sku_code", "i_id", "item_code", "sku_no")
    return {str(item.get(key) or "").strip() for key in keys if str(item.get(key) or "").strip()}


def _item_matches_sku(item: dict[str, Any], sku_code: str) -> bool:
    return str(sku_code or "").strip() in _item_sku_candidates(item)


def _find_order_item_by_sku(items: list[dict[str, Any]], sku_code: str) -> dict[str, Any] | None:
    for item in items:
        if _item_matches_sku(item, sku_code):
            return item
    return items[0] if items else None


def _same_order_item(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_oi_id = str(left.get("oi_id") or "").strip()
    right_oi_id = str(right.get("oi_id") or "").strip()
    if left_oi_id and right_oi_id:
        return left_oi_id == right_oi_id
    return left is right


def _exchange_create_data(
    item: dict[str, Any],
    *,
    is_del: bool,
    is_gift: bool,
    is_new: bool,
    qty_override: int | None = None,
) -> dict[str, Any]:
    sku_id = ""
    for key in ("sku_id", "product_code", "sku_code", "i_id", "item_code", "sku_no"):
        sku_id = str(item.get(key) or "").strip()
        if sku_id:
            break
    qty = _to_int(qty_override if qty_override is not None else item.get("qty"), default=1)
    price_value = item.get("price")
    if price_value in (None, ""):
        price_value = item.get("sale_price", item.get("salePrice"))
    price = 0.0 if is_gift and _to_float(price_value) == -99999 else _to_float(price_value)
    return {
        "sku_id": sku_id,
        "qty": qty,
        "price": price,
        "amount": f"{qty * price:.2f}",
        "is_gift": is_gift,
        "oi_id": item.get("oi_id") or 0,
        "is_del": is_del,
        "il_id": item.get("il_id"),
        "sku_type": item.get("sku_type") or "normal",
        "is_new": is_new,
        "remark": item.get("remark") or "",
    }


def _build_exchange_items_payload(
    *,
    source_oi_id: str,
    order_items: list[dict[str, Any]],
    target_skus: list[dict[str, Any]],
    keep_target_info: bool,
) -> dict[str, Any]:
    if not order_items:
        raise RuntimeError("ReloadOrdersV2 未返回订单商品，无法构造换货提交数据")
    if not target_skus:
        raise RuntimeError("未找到换入目标商品，无法构造换货提交数据")

    _ = keep_target_info
    source_item = next(
        (item for item in order_items if str(item.get("oi_id") or "").strip() == str(source_oi_id or "").strip()),
        None,
    )
    source_item = source_item or order_items[0]
    source_qty = _to_int(source_item.get("qty"), default=1)

    items = [
        _exchange_create_data(
            source_item,
            is_del=True,
            is_gift=_to_bool(source_item.get("is_gift")),
            is_new=False,
        )
    ]
    for target in target_skus:
        if len(target_skus) == 1:
            target_qty = source_qty
        else:
            target_qty = _to_int(target.get("qty"), default=0)
            if target_qty <= 0:
                target_qty = 1
        items.append(
            _exchange_create_data(
                target,
                is_del=False,
                is_gift=False,
                is_new=True,
                qty_override=target_qty,
            )
        )
    for item in order_items:
        if _same_order_item(item, source_item):
            continue
        items.append(
            _exchange_create_data(
                item,
                is_del=False,
                is_gift=_to_bool(item.get("is_gift")),
                is_new=False,
            )
        )
    return {"items": items}


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _extract_reloaded_order_items(payload: Any) -> list[dict[str, Any]]:
    queue: list[Any] = [payload]
    while queue:
        value = _decode_json_value(queue.pop(0))
        if isinstance(value, list):
            if all(isinstance(item, dict) and "sku_id" in item for item in value):
                return value
            queue.extend(value)
            continue
        if not isinstance(value, dict):
            continue

        data = value.get("data")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return [item for item in data["items"] if isinstance(item, dict)]
        if isinstance(value.get("items"), list):
            return [item for item in value["items"] if isinstance(item, dict)]

        for key in ("raw", "return_value", "ReturnValue", "data", "Data", "result", "Result"):
            child = value.get(key)
            if child is not None:
                queue.append(child)
    return []


def _call_jtable_method(method_name: str, args: list[Any]) -> dict[str, Any]:
    session, cookie, order_url, form_template = _load_order_session()
    url = _base_order_url(order_url)
    callback_param = {"Method": method_name, "Args": [str(item) for item in args], "CallControl": "{page}"}
    form = _build_request_form(method_name, callback_param, form_template=form_template)
    form["__CALLBACKID"] = "JTable1"
    headers = _submit_headers({}, session, cookie, url)
    headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    headers.setdefault("X-Requested-With", "XMLHttpRequest")
    headers.setdefault("Referer", url)
    with build_client(follow_redirects=True, timeout=60.0) as client:
        response = client.post(url, headers=headers, data=form)
        response.raise_for_status()
    payload = _extract_json_payload(response.text)
    result = _jtable_result_payload(payload)
    result["method"] = method_name
    return result


def _iter_item_sku_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    value = _decode_json_value(payload)
    if isinstance(value, list):
        for item in value:
            rows.extend(_iter_item_sku_rows(item))
        return rows
    if not isinstance(value, dict):
        return rows
    if "sku_id" in value:
        rows.append(value)
    for key in ("data", "Data", "rows", "Rows", "datas", "Datas", "items", "Items", "list", "List"):
        child = value.get(key)
        if child is not None:
            rows.extend(_iter_item_sku_rows(child))
    return rows


def _query_exchange_target_skus(
    target_sku_code: str,
    *,
    wms_co_id: str | None = None,
) -> list[dict[str, Any]]:
    session, cookie, _order_url, _form_template = _load_order_session()
    co_id = _extract_cookie_value(cookie, "u_co_id") or str(wms_co_id or "").strip()
    uid = _extract_cookie_value(cookie, "u_id")
    warehouse_id = str(wms_co_id or co_id).strip()
    suffix = f"&wms_warhouse_co_id={warehouse_id}" if warehouse_id else ""
    url = f"https://apiweb.erp321.com/webapi/ItemApi/ItemSku/GetPageListV2ForOrder?__from=web_component{suffix}"
    body = {
        "page": {"currentPage": 1, "pageSize": 50, "hasPageInfo": False, "pageAction": 1},
        "data": {
            "sku_id": target_sku_code,
            "enabled": "1",
            "queryFlds": [
                "pic",
                "i_id",
                "sku_id",
                "labels",
                "pickable_qty",
                "saleable_qty",
                "purchase_qty",
                "sku_code",
                "name",
                "short_name",
                "supplier_i_id",
                "supplier_sku_id",
                "brand",
                "properties_value",
                "category",
                "vc_name",
                "sale_price",
                "supplier_name",
                "saleable_qty2",
                "sale_price",
                "sale_price",
                "consignor_co_name",
            ],
            "OrderWmsCoId": _to_int(warehouse_id, default=0) or warehouse_id,
        },
        "ip": "",
        "coid": co_id,
        "uid": uid,
    }
    headers = _submit_headers(
        {},
        session,
        cookie,
        "https://src.erp321.com/erp-components/goods-selector/default-goods-selector",
    )
    headers["Content-Type"] = "application/json;charset=UTF-8"
    headers["Accept"] = "application/json, text/plain, */*"
    headers["Origin"] = "https://src.erp321.com"
    headers["Referer"] = "https://src.erp321.com/erp-components/goods-selector/default-goods-selector"
    with build_client(follow_redirects=True, timeout=60.0) as client:
        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        payload = _response_payload(response)

    rows = _iter_item_sku_rows(payload)
    exact = [row for row in rows if str(row.get("sku_id") or "").strip() == target_sku_code]
    if not exact:
        preview_codes = [str(row.get("sku_id") or "").strip() for row in rows[:10]]
        raise RuntimeError(f"商品选择器接口未找到目标商品 {target_sku_code}，返回商品编码：{preview_codes}")
    return exact[:1]


def _submit_exchange_api_flow(
    *,
    final_payload: dict[str, Any],
    order_no: str,
    resolved: dict[str, Any],
) -> dict[str, Any]:
    internal_order_id = str(final_payload.get("internal_order_id") or "").strip()
    return_sku_code = str(final_payload.get("sku_code") or "").strip()
    target_sku_code = str(final_payload.get("exchange_sku_code") or return_sku_code).strip()
    if not internal_order_id:
        raise RuntimeError("未解析到聚水潭内部订单 ID，无法执行换货")
    if not return_sku_code:
        raise RuntimeError("未解析到原订单商品编码，无法执行换货")
    if not target_sku_code:
        raise RuntimeError("未解析到目标换货商品编码")

    before_result = _call_jtable_method("ReloadOrdersV2", [internal_order_id, "true"])
    order_items = _extract_reloaded_order_items(before_result)
    if not order_items:
        order_items = [item for item in resolved.get("raw_items") or [] if isinstance(item, dict)]
    source_item = _find_order_item_by_sku(order_items, return_sku_code)
    if source_item is None:
        raise RuntimeError(f"ReloadOrdersV2 未找到原商品 {return_sku_code}，无法执行换货")
    source_oi_id = str(source_item.get("oi_id") or "").strip()
    if not source_oi_id:
        raise RuntimeError(f"原商品 {return_sku_code} 缺少 oi_id，无法执行换货")

    target_skus = _query_exchange_target_skus(
        target_sku_code,
        wms_co_id=str(resolved.get("wms_co_id") or resolved.get("co_id") or "").strip() or None,
    )
    keep_target_info = False
    items_payload = _build_exchange_items_payload(
        source_oi_id=source_oi_id,
        order_items=order_items,
        target_skus=target_skus,
        keep_target_info=keep_target_info,
    )
    exchange_items_json = json.dumps(items_payload, ensure_ascii=False, separators=(",", ":"))
    submit_payload = {
        **final_payload,
        "exchange_items_json": exchange_items_json,
        "keep_target_info": str(keep_target_info).lower(),
    }
    template = _build_exchange_submit_candidate(submit_payload)
    submit_result = _submit_jtable_call(template=template, final_payload=submit_payload, order_no=order_no)

    after_items: list[dict[str, Any]] = []
    for _attempt in range(3):
        after_result = _call_jtable_method("ReloadOrdersV2", [internal_order_id, "true"])
        after_items = _extract_reloaded_order_items(after_result)
        if any(_item_matches_sku(item, target_sku_code) for item in after_items):
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"ChangeBatchItem 已返回，但订单行未验证到目标商品 {target_sku_code}")

    return {
        "submitted": True,
        "request_kind": "jtable_call",
        "submit_method": "ChangeBatchItem",
        "order_no": order_no,
        "internal_order_id": internal_order_id,
        "before_sku_code": return_sku_code,
        "after_sku_code": target_sku_code,
        "qty": _to_int(source_item.get("qty"), default=1),
        "source_oi_id": source_oi_id,
        "keep_target_info": keep_target_info,
        "items_payload": items_payload,
        "submit_result": submit_result,
        "items": after_items,
    }


# --------------------------------------------------------------------------- #
# learn：用 9222 Chrome 探索换货 / 补发入口（绝不提交）
# --------------------------------------------------------------------------- #
def _safe_screenshot(page: Any, path: Path) -> str | None:
    # 后台窗口合成器不产帧，截图前必须 bring_to_front() 否则会无限挂起。
    try:
        page.bring_to_front()
    except Exception:
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return None


def _wait_for_frame_by_url(page: Any, url_part: str, *, timeout_ms: int = 15000) -> Any | None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for frame in page.frames:
            if url_part in str(getattr(frame, "url", "")):
                return frame
        page.wait_for_timeout(250)
    return None


def _find_order_list_frame(page: Any) -> Any | None:
    for frame in page.frames:
        if "app/order/order/list.aspx" in str(getattr(frame, "url", "")):
            return frame
    main_frame = getattr(page, "main_frame", None)
    if main_frame and "app/order/order/list.aspx" in str(getattr(main_frame, "url", "")):
        return main_frame
    return None


def _open_exchange_picker_from_order_list(
    frame: Any,
    *,
    order_no: str,
    internal_order_id: str,
    return_sku_code: str,
) -> dict[str, Any]:
    open_script = """
    ({ orderNo, internalOrderId, returnSkuCode }) => {
      const normalize = (value) => String(value == null ? '' : value).trim();
      const rows = () => (window.jTable && Array.isArray(jTable.Rows)) ? jTable.Rows : [];
      const rowMatches = (row) => {
        const data = row && row.Data ? row.Data : {};
        return normalize(data.o_id) === normalize(internalOrderId)
          || normalize(data.outer_so_id) === normalize(orderNo)
          || normalize(data.so_id) === normalize(orderNo)
          || normalize(data.raw_so_id) === normalize(orderNo)
          || normalize(data.pre_so_id) === normalize(orderNo);
      };
      const row = rows().find(rowMatches);
      if (!row) {
        if (
          window.jTable
          && typeof jTable.NewSearch === 'function'
          && typeof jTable.AddSearch === 'function'
          && typeof jTable.Search === 'function'
        ) {
          jTable.NewSearch();
          jTable.AddSearch('outer_so_id', orderNo, '@=');
          jTable.Search();
          return { opened: false, search_started: true, search_field: 'outer_so_id' };
        }
        return { opened: false, error: 'jTable search is not available' };
      }

      try {
        if (row.HtmlRow && typeof row.HtmlRow.click === 'function') {
          row.HtmlRow.click();
        }
      } catch (err) {}
      try {
        jTable.CurrentJTableRow = row;
      } catch (err) {}

      let itemCellFound = false;
      try {
        const cell = typeof row.HtmlCell === 'function' ? row.HtmlCell('its') : null;
        if (cell && typeof window.ShowFullItems === 'function') {
          ShowFullItems(window.$ ? window.$(cell) : cell, row.Data);
          itemCellFound = true;
        }
      } catch (err) {}

      const items = Array.isArray(row.Data && row.Data.items) ? row.Data.items : [];
      const sourceItem = items.find((item) => normalize(item.sku_id) === normalize(returnSkuCode)) || items[0] || null;
      if (!sourceItem) {
        return { opened: false, error: 'matched order has no item rows', order_found: true };
      }
      if (typeof window.ChangeBatchItem !== 'function') {
        return { opened: false, error: 'ChangeBatchItem is not available', order_found: true };
      }

      let preScrollTop = null;
      try {
        const cell = typeof row.HtmlCell === 'function' ? row.HtmlCell('its') : null;
        const scroll = cell && window.$ ? window.$(cell).find('.fullItemScrolDom') : null;
        preScrollTop = scroll && scroll.length ? scroll.scrollTop() : null;
      } catch (err) {}

      window.__codex_exchange_probe = {
        order_no: orderNo,
        internal_order_id: normalize(row.Data && row.Data.o_id),
        return_sku_code: normalize(sourceItem.sku_id),
        change_batch_item_opened: true,
        change_batch_item_confirmed: false,
      };
      ChangeBatchItem(sourceItem, preScrollTop);
      return {
        opened: true,
        order_found: true,
        internal_order_id: normalize(row.Data && row.Data.o_id),
        online_order_id: normalize(row.Data && row.Data.so_id),
        outer_so_id: normalize(row.Data && row.Data.outer_so_id),
        order_status: normalize(row.Data && row.Data.status),
        item_cell_found: itemCellFound,
        source_row: {
          sku_id: normalize(sourceItem.sku_id),
          i_id: normalize(sourceItem.i_id),
          qty: Number(sourceItem.qty || 1),
          oi_id: normalize(sourceItem.oi_id),
          o_id: normalize(sourceItem.o_id || (row.Data && row.Data.o_id)),
        },
        submit_method: 'ChangeBatchItem',
        submit_called: false,
      };
    }
    """
    args = {
        "orderNo": order_no,
        "internalOrderId": internal_order_id,
        "returnSkuCode": return_sku_code,
    }
    opened = frame.evaluate(open_script, args)
    if isinstance(opened, dict) and opened.get("search_started"):
        frame.wait_for_function(
            """
            ({ orderNo, internalOrderId }) => {
              const normalize = (value) => String(value == null ? '' : value).trim();
              if (!window.jTable || !Array.isArray(jTable.Rows)) return false;
              return jTable.Rows.some((row) => {
                const data = row && row.Data ? row.Data : {};
                return normalize(data.o_id) === normalize(internalOrderId)
                  || normalize(data.outer_so_id) === normalize(orderNo)
                  || normalize(data.so_id) === normalize(orderNo)
                  || normalize(data.raw_so_id) === normalize(orderNo)
                  || normalize(data.pre_so_id) === normalize(orderNo);
              });
            }
            """,
            arg={"orderNo": order_no, "internalOrderId": internal_order_id},
            timeout=15000,
        )
        opened = frame.evaluate(open_script, args)
    return opened if isinstance(opened, dict) else {"opened": False, "raw": opened}


def _search_target_in_exchange_picker(frame: Any, *, target_sku_code: str) -> dict[str, Any]:
    search_result: dict[str, Any]
    try:
        # 商品选择器默认会把「款式编码」带成原商品名；不清空会和目标商品编码组合过滤，导致搜不到换入商品。
        try:
            frame.get_by_placeholder("款式编码").first.fill("", timeout=5000)
        except Exception:
            pass
        frame.get_by_placeholder("商品编码").first.fill(target_sku_code, timeout=5000)
        frame.get_by_text(re.compile(r"搜\s*索")).last.click(timeout=5000)
        search_result = {
            "searched": True,
            "field_placeholder": "商品编码",
            "cleared_style_code": True,
            "search_clicked": True,
        }
    except Exception as exc:
        search_result = {
            "searched": False,
            "error": str(exc),
            "inputs": frame.evaluate(
                """
                () => [...document.querySelectorAll('input')]
                  .map((input) => ({ placeholder: input.placeholder || '', value: input.value || '' }))
                  .slice(0, 30)
                """
            ),
        }
        raise RuntimeError(f"商品选择器搜索目标商品失败：{search_result}") from exc
    frame.wait_for_function(
        """
        (targetSkuCode) => {
          const text = document.body ? (document.body.innerText || '') : '';
          return text.includes(targetSkuCode);
        }
        """,
        arg=target_sku_code,
        timeout=15000,
    )
    select_result = frame.evaluate(
        """
        ({ targetSkuCode }) => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const rows = [...document.querySelectorAll('tr,.art-table-row')]
            .filter((row) => visible(row) && (row.innerText || '').includes(targetSkuCode));
          const row = rows[0] || null;
          if (!row) {
            return { target_found: false, selected: false };
          }
          const checkbox = row.querySelector('input[type="checkbox"]');
          const wasSelected = Boolean(
            (checkbox && checkbox.checked)
            || String(row.className || '').includes('checked')
            || String(row.className || '').includes('highlight')
          );
          if (!wasSelected) {
            const clickTarget = checkbox || row;
            clickTarget.click();
          }
          const selected = Boolean(
            (checkbox && checkbox.checked)
            || String(row.className || '').includes('checked')
            || String(row.className || '').includes('highlight')
          );
          return {
            target_found: true,
            selected,
            confirm_clicked: false,
            row_text: (row.innerText || '').trim().slice(0, 1000),
          };
        }
        """,
        {"targetSkuCode": target_sku_code},
    )
    return {
        "search": search_result if isinstance(search_result, dict) else {"raw": search_result},
        "selection": select_result if isinstance(select_result, dict) else {"raw": select_result},
    }


def _snapshot_exchange_order_items(
    frame: Any, *, order_no: str, internal_order_id: str
) -> dict[str, Any]:
    snapshot = frame.evaluate(
        """
        ({ orderNo, internalOrderId }) => {
          const normalize = (value) => String(value == null ? '' : value).trim();
          const rows = () => (window.jTable && Array.isArray(jTable.Rows)) ? jTable.Rows : [];
          const rowMatches = (row) => {
            const data = row && row.Data ? row.Data : {};
            return normalize(data.o_id) === normalize(internalOrderId)
              || normalize(data.outer_so_id) === normalize(orderNo)
              || normalize(data.so_id) === normalize(orderNo)
              || normalize(data.raw_so_id) === normalize(orderNo)
              || normalize(data.pre_so_id) === normalize(orderNo);
          };
          const itemSku = (item) => {
            const keys = ['sku_id', 'product_code', 'sku_code', 'sku_no', 'item_code', 'item_no', 'i_id', 'bn'];
            for (const key of keys) {
              const value = normalize(item && item[key]);
              if (value) return value;
            }
            return '';
          };
          const row = rows().find(rowMatches);
          if (!row) return { found: false, items: [] };
          const data = row.Data || {};
          const items = Array.isArray(data.items) ? data.items : [];
          return {
            found: true,
            internal_order_id: normalize(data.o_id),
            online_order_id: normalize(data.so_id || data.raw_so_id || data.pre_so_id),
            outer_so_id: normalize(data.outer_so_id),
            order_status: normalize(data.status || data.order_status || data.buyer_status),
            items: items.map((item) => ({
              sku_code: itemSku(item),
              sku_id: normalize(item && item.sku_id),
              i_id: normalize(item && item.i_id),
              product_code: normalize(item && item.product_code),
              product_name: normalize(item && (item.product_name || item.sku_name || item.name || item.i_name)),
              qty: Number((item && (item.qty || item.num || item.count)) || 1),
              oi_id: normalize(item && item.oi_id),
              o_id: normalize(item && item.o_id),
            })),
          };
        }
        """,
        {"orderNo": order_no, "internalOrderId": internal_order_id},
    )
    return snapshot if isinstance(snapshot, dict) else {"found": False, "raw": snapshot}


def _wait_for_exchange_target_applied(
    frame: Any, *, order_no: str, internal_order_id: str, target_sku_code: str
) -> dict[str, Any]:
    frame.wait_for_function(
        """
        ({ orderNo, internalOrderId, targetSkuCode }) => {
          const normalize = (value) => String(value == null ? '' : value).trim();
          const rows = () => (window.jTable && Array.isArray(jTable.Rows)) ? jTable.Rows : [];
          const rowMatches = (row) => {
            const data = row && row.Data ? row.Data : {};
            return normalize(data.o_id) === normalize(internalOrderId)
              || normalize(data.outer_so_id) === normalize(orderNo)
              || normalize(data.so_id) === normalize(orderNo)
              || normalize(data.raw_so_id) === normalize(orderNo)
              || normalize(data.pre_so_id) === normalize(orderNo);
          };
          const itemValues = (item) => [
            item && item.sku_id,
            item && item.product_code,
            item && item.sku_code,
            item && item.sku_no,
            item && item.item_code,
            item && item.item_no,
            item && item.i_id,
            item && item.bn,
          ].map(normalize).filter(Boolean);
          const row = rows().find(rowMatches);
          if (!row) return false;
          const items = Array.isArray(row.Data && row.Data.items) ? row.Data.items : [];
          return items.some((item) => itemValues(item).includes(normalize(targetSkuCode)));
        }
        """,
        arg={
            "orderNo": order_no,
            "internalOrderId": internal_order_id,
            "targetSkuCode": target_sku_code,
        },
        timeout=20000,
    )
    return _snapshot_exchange_order_items(
        frame,
        order_no=order_no,
        internal_order_id=internal_order_id,
    )


def _click_exchange_picker_confirm(frame: Any) -> dict[str, Any]:
    errors: list[str] = []
    exact_confirm = re.compile(r"^\s*确\s*定\s*$")
    locators = (
        ("role_button", lambda: frame.get_by_role("button", name=exact_confirm)),
        ("button_text", lambda: frame.locator("button").filter(has_text=exact_confirm)),
        ("text", lambda: frame.get_by_text(exact_confirm)),
    )
    for method, locator_factory in locators:
        try:
            locator = locator_factory()
            if locator.count() <= 0:
                continue
            locator.last.click(timeout=5000)
            return {"clicked_confirm": True, "method": method}
        except Exception as exc:
            errors.append(f"{method}: {exc}")

    clicked = frame.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
          };
          const textOf = (el) => (el.innerText || el.textContent || '').trim();
          const candidates = [...document.querySelectorAll('button,a,[role="button"],span,div')]
            .filter((el) => visible(el) && textOf(el) === '确定');
          const buttonish = candidates.filter((el) => {
            const cls = String(el.className || '').toLowerCase();
            return el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button'
              || cls.includes('btn') || cls.includes('button');
          });
          const list = buttonish.length ? buttonish : candidates;
          const target = list.length ? list[list.length - 1] : null;
          if (!target) return { clicked_confirm: false, error: 'confirm button not found' };
          target.click();
          return {
            clicked_confirm: true,
            method: 'dom_click',
            tag: target.tagName,
            text: textOf(target),
            class_name: String(target.className || ''),
          };
        }
        """
    )
    if isinstance(clicked, dict) and clicked.get("clicked_confirm"):
        return clicked
    raise RuntimeError(f"商品选择器「确定」按钮点击失败：{clicked}；locator_errors={errors}")


def _submit_exchange_browser_flow(*, final_payload: dict[str, Any], order_no: str) -> dict[str, Any]:
    """旧版浏览器确认路径；保留给诊断，submit 默认不调用。"""
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

    internal_order_id = str(final_payload.get("internal_order_id") or "").strip()
    return_sku_code = str(final_payload.get("sku_code") or "").strip()
    target_sku_code = str(final_payload.get("exchange_sku_code") or return_sku_code).strip()
    if not internal_order_id:
        raise RuntimeError("未解析到聚水潭内部订单 ID，无法执行换货")
    if not return_sku_code:
        raise RuntimeError("未解析到原订单商品编码，无法执行换货")
    if not target_sku_code:
        raise RuntimeError("未解析到目标换货商品编码")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        with managed_work_page(context, "jst.exchange_resend.submit") as page:
            try:
                page.goto(
                    "https://www.erp321.com/app/order/order/list.aspx",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                pass
            surface_jst_login_if_needed(page)
            page.wait_for_timeout(2000)
            order_frame = _find_order_list_frame(page)
            if order_frame is None:
                raise RuntimeError("订单列表 iframe 未找到，无法执行换货")
            order_frame.wait_for_function(
                "() => window.jTable && Array.isArray(jTable.Rows)",
                timeout=15000,
            )

            before_snapshot = _snapshot_exchange_order_items(
                order_frame,
                order_no=order_no,
                internal_order_id=internal_order_id,
            )
            open_probe = _open_exchange_picker_from_order_list(
                order_frame,
                order_no=order_no,
                internal_order_id=internal_order_id,
                return_sku_code=return_sku_code,
            )
            if not open_probe.get("opened"):
                raise RuntimeError(f"行内换货商品选择器打开失败：{open_probe}")

            picker_frame = _wait_for_frame_by_url(page, "goods-selector", timeout_ms=15000)
            if picker_frame is None:
                raise RuntimeError("商品选择器 iframe 未出现")
            picker_probe = _search_target_in_exchange_picker(
                picker_frame,
                target_sku_code=target_sku_code,
            )
            selection = picker_probe.get("selection") if isinstance(picker_probe, dict) else {}
            if not (isinstance(selection, dict) and selection.get("target_found")):
                raise RuntimeError(f"商品选择器未找到目标商品 {target_sku_code}：{picker_probe}")

            confirm_result = _click_exchange_picker_confirm(picker_frame)
            after_snapshot = _wait_for_exchange_target_applied(
                order_frame,
                order_no=order_no,
                internal_order_id=internal_order_id,
                target_sku_code=target_sku_code,
            )
            items_after = after_snapshot.get("items") if isinstance(after_snapshot, dict) else []
            target_applied = any(
                target_sku_code
                in {
                    str((item or {}).get("sku_code") or "").strip(),
                    str((item or {}).get("sku_id") or "").strip(),
                    str((item or {}).get("i_id") or "").strip(),
                    str((item or {}).get("product_code") or "").strip(),
                }
                for item in (items_after or [])
                if isinstance(item, dict)
            )
            if not target_applied:
                raise RuntimeError(f"已点击「确定」，但订单行尚未变更为目标商品 {target_sku_code}：{after_snapshot}")
            return {
                "submitted": True,
                "submit_method": "ChangeBatchItem",
                "clicked_confirm": bool(confirm_result.get("clicked_confirm")),
                "confirm": confirm_result,
                "order_no": order_no,
                "internal_order_id": internal_order_id,
                "before_sku_code": return_sku_code,
                "after_sku_code": target_sku_code,
                "qty": int(final_payload.get("qty") or 1),
                "open_picker": open_probe,
                "picker": picker_probe,
                "before_items": before_snapshot.get("items") if isinstance(before_snapshot, dict) else [],
                "items": items_after,
            }


def _probe_exchange_draft_page(
    page: Any,
    *,
    order_no: str,
    out_dir: Path,
    timestamp: str,
    exchange_sku_code: str | None = None,
    qty: int = 1,
) -> dict[str, Any]:
    """打开订单列表行内换货商品选择器并搜索目标商品，但不点击「确定」。"""
    result: dict[str, Any] = {
        "steps_detected": [],
        "screenshot_paths": [],
        "exchange_draft_probe": {},
    }
    try:
        template = _confirmed_template()
        eligible_status = _eligible_statuses_for_mode(template, "exchange")
        resolved = _resolve_order(order_no, eligible_status=eligible_status)
        internal_order_id = str(resolved.get("internal_order_id") or "").strip()
        if not internal_order_id:
            raise RuntimeError("未解析到聚水潭内部订单 ID，无法打开订单列表换货入口")
        items = resolved.get("items") or []
        first_item = items[0] if items and isinstance(items[0], dict) else {}
        return_sku_code = str(first_item.get("product_code") or "").strip()
        target_sku_code = str(exchange_sku_code or return_sku_code).strip()
        if not return_sku_code:
            raise RuntimeError("未解析到原订单商品编码，无法打开行内换货入口")
        if not target_sku_code:
            raise RuntimeError("未解析到目标换货商品编码")

        result["steps_detected"].extend(_exchange_candidate_steps()[:2])
        page.goto(
            "https://www.erp321.com/app/order/order/list.aspx",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        surface_jst_login_if_needed(page)
        page.wait_for_timeout(2500)
        order_frame = _find_order_list_frame(page)
        if order_frame is None:
            raise RuntimeError("订单列表 iframe 未找到，无法打开行内换货入口")
        order_frame.wait_for_function(
            "() => window.jTable && Array.isArray(jTable.Rows)",
            timeout=15000,
        )

        open_probe = _open_exchange_picker_from_order_list(
            order_frame,
            order_no=order_no,
            internal_order_id=internal_order_id,
            return_sku_code=return_sku_code,
        )
        if not open_probe.get("opened"):
            raise RuntimeError(f"行内换货商品选择器打开失败：{open_probe}")
        result["steps_detected"].append(_exchange_candidate_steps()[2])
        shot = _safe_screenshot(page, out_dir / f"02_exchange_picker_open_{timestamp}.png")
        if shot:
            result["screenshot_paths"].append(shot)

        picker_frame = _wait_for_frame_by_url(page, "goods-selector", timeout_ms=15000)
        if picker_frame is None:
            raise RuntimeError("商品选择器 iframe 未出现")
        picker_probe = _search_target_in_exchange_picker(
            picker_frame,
            target_sku_code=target_sku_code,
        )
        result["steps_detected"].extend(_exchange_candidate_steps()[3:])
        shot = _safe_screenshot(page, out_dir / f"03_exchange_picker_target_{timestamp}.png")
        if shot:
            result["screenshot_paths"].append(shot)

        selection = picker_probe.get("selection") if isinstance(picker_probe, dict) else {}
        target_found = bool(isinstance(selection, dict) and selection.get("target_found"))
        result["exchange_draft_probe"] = {
            "loaded": True,
            "page_title": page.title(),
            "order_no": order_no,
            "internal_order_id": internal_order_id,
            "target_sku_code": target_sku_code,
            "qty": int(qty),
            "selected_type": "换货",
            "request_data": {"type": "换货"},
            "return_rows": [
                {
                    "type": "退货",
                    "sku_id": return_sku_code,
                    "qty": int(qty),
                    "source": "original_order_item",
                }
            ],
            "exchange_rows": [
                {
                    "type": "换货",
                    "sku_id": target_sku_code,
                    "qty": int(qty),
                    "source": "goods_selector",
                    "target_found": target_found,
                }
            ],
            "source_row": open_probe.get("source_row"),
            "open_picker": open_probe,
            "picker": picker_probe,
            "detected_methods": {"change_batch_item": True},
            "submit_method": "ChangeBatchItem",
            "confirm_clicked": False,
            "submit_called": False,
        }
    except Exception as exc:
        result["steps_detected"].append(
            {
                "stage": "exchange_draft_probe",
                "found": False,
                "error": str(exc),
            }
        )
    return result


def _explore_exchange_resend_page(
    *,
    order_no: str,
    mode: str,
    screenshot_dir: str | None,
    sku_code: str | None = None,
    qty: int = 1,
) -> dict[str, Any]:
    """在 9222 Chrome 上探索售后 / 换货 / 补发入口，记录候选步骤与截图。

    这是探索脚手架：不点击任何提交按钮，只观察页面上是否存在换货 / 补发相关入口，
    并把页面可见文案、命中关键字、截图落盘，供人工确认页面路径后再固化为模板。
    """
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

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if screenshot_dir:
        out_dir = Path(screenshot_dir).expanduser()
    else:
        out_dir = Path.cwd() / "runtime" / "screenshots" / EXCHANGE_RESEND_SCENE
    out_dir.mkdir(parents=True, exist_ok=True)

    screenshots: list[str] = []
    steps_detected: list[dict[str, Any]] = []
    page_texts: list[str] = []

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        with managed_work_page(context, "jst.exchange_resend.learn") as page:
            try:
                page.goto(
                    "https://www.erp321.com/app/order/order/list.aspx",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                pass
            surface_jst_login_if_needed(page)
            page.wait_for_timeout(2000)
            shot = _safe_screenshot(page, out_dir / f"01_order_list_{timestamp}.png")
            if shot:
                screenshots.append(shot)

            # 在订单列表页观察换货 / 补发 / 售后入口是否存在（只观察，不点击提交）
            for keyword in AFTERSALE_ENTRY_KEYWORDS:
                try:
                    locator = page.get_by_text(keyword, exact=False)
                    count = locator.count()
                except Exception:
                    count = 0
                steps_detected.append(
                    {
                        "stage": "order_list",
                        "keyword": keyword,
                        "found": bool(count),
                        "match_count": int(count),
                    }
                )

            try:
                page_texts = page.evaluate(
                    """
                    () => {
                      const visible = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                      };
                      return [...document.querySelectorAll('button,a,span,label')]
                        .filter(visible)
                        .map(el => (el.innerText || el.textContent || '').trim())
                        .filter(Boolean)
                        .slice(0, 400);
                    }
                    """
                )
            except Exception:
                page_texts = []

            if mode == "exchange":
                probe = _probe_exchange_draft_page(
                    page,
                    order_no=order_no,
                    out_dir=out_dir,
                    timestamp=timestamp,
                    exchange_sku_code=sku_code,
                    qty=qty,
                )
                steps_detected.extend(probe.get("steps_detected") or [])
                screenshots.extend(probe.get("screenshot_paths") or [])

    profile = {
        "site": JST_SITE,
        "scene": EXCHANGE_RESEND_SCENE,
        "confirmed": False,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "source": "sessionhub_9222",
        "order_no": order_no,
        "mode": mode,
        "steps_detected": steps_detected,
        "page_texts": page_texts,
        "screenshot_paths": screenshots,
        "exchange_draft_probe": probe.get("exchange_draft_probe") if mode == "exchange" else {},
        "notes": {
            "boundary": "仅探索售后/换货/补发入口；换货只打开商品选择器、搜索并选中目标，不点击确定。",
            "next": "人工核对页面路径后，固化 confirmed exchange browser flow，submit 才会真正提交。",
        },
    }
    profile_path = Path.cwd() / "data" / "jst" / f"{EXCHANGE_RESEND_SCENE}_explore_{timestamp}.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "steps_detected": steps_detected,
        "screenshot_paths": screenshots,
        "page_texts": page_texts,
        "exchange_draft_probe": profile["exchange_draft_probe"],
        "profile_path": str(profile_path),
    }


# --------------------------------------------------------------------------- #
# 对外 handler
# --------------------------------------------------------------------------- #
def learn_order_exchange_resend(
    *,
    order_no: str,
    mode: str,
    reason: str | None = None,
    remark: str | None = None,
    sku_code: str | None = None,
    qty: int = 1,
    screenshot_dir: str | None = None,
) -> CommandResponse:
    if not order_no:
        raise RuntimeError("请传入 --order-no")
    mode = _normalize_mode(mode)
    data = _base_data(order_no, mode, "learn")
    data["source"] = "9222_chrome"

    exploration = _explore_exchange_resend_page(
        order_no=order_no,
        mode=mode,
        screenshot_dir=screenshot_dir,
        sku_code=sku_code,
        qty=qty,
    )
    data["steps_detected"] = exploration["steps_detected"]
    data["screenshot_paths"] = exploration["screenshot_paths"]
    data["profile_path"] = exploration["profile_path"]

    # learn 同时尽力解析订单，给出眼下能确定的资格信息（探索不依赖订单是否找到）
    try:
        template = _confirmed_template()
        eligible_status = _eligible_statuses_for_mode(template, mode)
        resolved = _resolve_order(order_no, eligible_status=eligible_status)
        eligible, reason_text, sku_matched = _evaluate_eligibility(
            resolved, mode=mode, sku_code=sku_code, eligible_status=eligible_status
        )
        data["found_order"] = resolved.get("found_order", False)
        data["order_status"] = resolved.get("order_status")
        data["eligible"] = eligible
        data["ineligible_reason"] = reason_text
        data["sku_matched"] = sku_matched
        if resolved.get("found_order"):
            data["final_payload"] = _build_final_payload(
                resolved, mode=mode, reason=reason, remark=remark, sku_code=sku_code, qty=qty
            )
            if mode == "exchange":
                probe = exploration.get("exchange_draft_probe") or {}
                rows_preview = []
                if isinstance(probe, dict):
                    rows_preview.extend(probe.get("return_rows") or [])
                    rows_preview.extend(probe.get("exchange_rows") or [])
                data["exchange_draft_probe"] = probe
                data["exchange_submit_candidate"] = _build_exchange_submit_candidate(
                    data["final_payload"],
                    request_data_preview=probe.get("request_data") if isinstance(probe, dict) else None,
                    rows_preview=rows_preview or None,
                )
    except Exception as exc:  # 探索阶段订单解析失败不影响 learn 主目标
        data["order_resolve_error"] = str(exc)

    if mode == "exchange" or _template_supports_mode(_confirmed_template(), mode):
        data["pending_confirmation"] = []
    else:
        data["pending_confirmation"] = [
            "换货 / 补发页面路径尚未人工确认，已保存探索截图与候选入口。",
            "请核对截图与 steps_detected，确认正确入口后再固化提交模板。",
        ]
    context_path = write_runtime_context(
        task_name="jst_order_exchange_resend_learn",
        status="success",
        inputs={"order_no": order_no, "mode": mode},
        outputs={
            "screenshot_count": len(data["screenshot_paths"]),
            "profile_path": data.get("profile_path"),
        },
        artifacts=data["screenshot_paths"],
    )
    data["context_path"] = str(context_path)
    return CommandResponse(
        success=True,
        platform="jst",
        command="order exchange-resend learn",
        data=data,
    )


def preview_order_exchange_resend(
    *,
    order_no: str,
    mode: str,
    reason: str | None = None,
    remark: str | None = None,
    sku_code: str | None = None,
    qty: int = 1,
) -> CommandResponse:
    if not order_no:
        raise RuntimeError("请传入 --order-no")
    mode = _normalize_mode(mode)
    data = _base_data(order_no, mode, "preview")

    template = _confirmed_template()
    eligible_status = _eligible_statuses_for_mode(template, mode)
    resolved = _resolve_order(order_no, eligible_status=eligible_status)
    eligible, reason_text, sku_matched = _evaluate_eligibility(
        resolved, mode=mode, sku_code=sku_code, eligible_status=eligible_status
    )
    data["found_order"] = resolved.get("found_order", False)
    data["matched_filter"] = resolved.get("matched_filter")
    data["order_status"] = resolved.get("order_status")
    data["eligible"] = eligible
    data["ineligible_reason"] = reason_text
    data["sku_matched"] = sku_matched
    if resolved.get("found_order"):
        data["final_payload"] = _build_final_payload(
            resolved, mode=mode, reason=reason, remark=remark, sku_code=sku_code, qty=qty
        )
        if mode == "exchange":
            data["exchange_submit_candidate"] = _build_exchange_submit_candidate(data["final_payload"])
    partial_conflict = _partial_resend_conflict(mode, template, sku_code, qty)
    if partial_conflict:
        data.setdefault("warnings", []).append(partial_conflict)
    if eligible and mode != "exchange" and not _template_supports_mode(template, mode):
        data["pending_confirmation"] = [
            "预览通过，但换货 / 补发页面提交路径尚未学习并人工确认。",
            "真实提交前请先运行 learn 探索并固化模板。",
        ]

    context_path = write_runtime_context(
        task_name="jst_order_exchange_resend_preview",
        status="success",
        inputs={"order_no": order_no, "mode": mode, "sku_code": sku_code},
        outputs={
            "found_order": data["found_order"],
            "order_status": data["order_status"],
            "eligible": eligible,
        },
    )
    data["context_path"] = str(context_path)
    return CommandResponse(
        success=True,
        platform="jst",
        command="order exchange-resend preview",
        data=data,
    )


def submit_order_exchange_resend(
    *,
    order_no: str,
    mode: str,
    confirm_order_no: str | None = None,
    reason: str | None = None,
    remark: str | None = None,
    sku_code: str | None = None,
    qty: int = 1,
    execute: bool = False,
) -> CommandResponse:
    if not order_no:
        raise RuntimeError("请传入 --order-no")
    mode = _normalize_mode(mode)
    if not execute:
        raise RuntimeError("submit 必须显式传入 --execute")
    if not confirm_order_no:
        raise RuntimeError("真实提交必须传入 --confirm-order-no 二次确认")
    if str(confirm_order_no).strip() != str(order_no).strip():
        raise RuntimeError("--confirm-order-no 与 --order-no 不一致，已拒绝提交")

    template = _confirmed_template()

    # 整单补发模板下显式传 --sku-code / --qty 会误导 → 提交前直接拒绝（fail fast，不打平台）
    partial_conflict = _partial_resend_conflict(mode, template, sku_code, qty)
    if partial_conflict:
        raise RuntimeError(partial_conflict)

    data = _base_data(order_no, mode, "submit")
    eligible_status = _eligible_statuses_for_mode(template, mode)

    # 阶段一（只读、幂等）：解析订单 + 资格判断。这一段没有任何不可逆动作，auth 失效可安全重试。
    # 重试边界**只**包到这里：一旦进入阶段二的真实提交（ChangeBatchItem / CreateReissueOrderAllItem），
    # 提交不可逆，绝不能因后续步骤（如提交后复查 ReloadOrdersV2 撞上 token 过期）再触发重跑，
    # 否则会对已换货 / 已补发的订单重复提交、改乱订单。
    auth_refresh_applied = False
    retried_for_auth = False
    while True:
        try:
            resolved = _resolve_order(order_no, eligible_status=eligible_status)
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

    eligible, reason_text, sku_matched = _evaluate_eligibility(
        resolved,
        mode=mode,
        sku_code=sku_code,
        eligible_status=eligible_status,
    )
    data["found_order"] = resolved.get("found_order", False)
    data["matched_filter"] = resolved.get("matched_filter")
    data["order_status"] = resolved.get("order_status")
    data["eligible"] = eligible
    data["ineligible_reason"] = reason_text
    data["sku_matched"] = sku_matched

    if not eligible:
        # 找不到订单 / 状态不允许 / 商品不匹配 → 必须停止
        context_path = write_runtime_context(
            task_name="jst_order_exchange_resend_submit",
            status="stopped",
            inputs={"order_no": order_no, "mode": mode},
            outputs={"eligible": False, "reason": reason_text},
        )
        data["context_path"] = str(context_path)
        data["pending_confirmation"] = [reason_text or "订单不满足换货 / 补发条件"]
        return CommandResponse(
            success=True,
            platform="jst",
            command="order exchange-resend submit",
            data=data,
        )

    # 资格通过：先输出 final_payload（安全红线第 9 条），再进入阶段二的不可逆提交（不再做 auth 重试）
    data["final_payload"] = _build_final_payload(
        resolved, mode=mode, reason=reason, remark=remark, sku_code=sku_code, qty=qty
    )
    if mode == "exchange":
        data["exchange_submit_candidate"] = _build_exchange_submit_candidate(data["final_payload"])
        result = _submit_exchange_api_flow(
            final_payload=data["final_payload"],
            order_no=order_no,
            resolved=resolved,
        )
        data["submitted"] = True
        data["result"] = result
        data["pending_confirmation"] = []
        if auth_refresh_applied:
            data["auth_refresh_applied"] = True
        context_path = write_runtime_context(
            task_name="jst_order_exchange_resend_submit",
            status="success",
            inputs={"order_no": order_no, "mode": mode, "execute": execute},
            outputs={"submitted": True, "result": result},
        )
        data["context_path"] = str(context_path)
        return CommandResponse(
            success=True,
            platform="jst",
            command="order exchange-resend submit",
            data=data,
        )
    if template is None or not _template_supports_mode(template, mode):
        # 补发页面路径未学习并人工确认 → 停在待确认，绝不硬提交
        reason_summary = (
            "页面路径未确认" if template is None else f"{mode} 模板未确认"
        )
        data["pending_confirmation"] = [
            "换货 / 补发页面提交路径尚未学习并人工确认。"
            if template is None
            else f"现有 confirmed 模板不支持 {mode}，已拒绝真实提交。",
            "已输出 final_payload，但为避免不可逆误操作，未点击任何最终提交按钮。",
            f"请先运行：ops --json jst order exchange-resend learn --order-no {order_no} --mode {mode} --dry-run，"
            "核对截图后固化 data/jst/order_exchange_resend_template.json（confirmed=true）再提交。",
        ]
        context_path = write_runtime_context(
            task_name="jst_order_exchange_resend_submit",
            status="pending_confirmation",
            inputs={"order_no": order_no, "mode": mode, "execute": execute},
            outputs={"submitted": False, "reason": reason_summary},
        )
        data["context_path"] = str(context_path)
        return CommandResponse(
            success=True,
            platform="jst",
            command="order exchange-resend submit",
            data=data,
        )

    result = _submit_from_template(template=template, final_payload=data["final_payload"], order_no=order_no)
    data["submitted"] = True
    data["result"] = result
    if auth_refresh_applied:
        data["auth_refresh_applied"] = True
    context_path = write_runtime_context(
        task_name="jst_order_exchange_resend_submit",
        status="success",
        inputs={"order_no": order_no, "mode": mode, "execute": execute},
        outputs={"submitted": True, "result": result},
    )
    data["context_path"] = str(context_path)
    return CommandResponse(
        success=True,
        platform="jst",
        command="order exchange-resend submit",
        data=data,
    )
