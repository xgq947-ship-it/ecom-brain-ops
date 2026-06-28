from __future__ import annotations

import json
import logging
from json import JSONDecoder
from typing import Any
from urllib.parse import urlparse

from .site_config import get_scene_config
from .session_store import SessionStore, now_iso


LOGIN_MARKERS = ("login", "登录", "passport", "forbidden", "unauthorized")
TMALL_FAILURE_MARKERS = (
    "未登录",
    "请登录",
    "重新登录",
    "登录失效",
    "权限",
    "forbidden",
    "unauthorized",
    "token失效",
    "token expired",
)
TMALL_FAILURE_CODE_MARKERS = ("login", "nologin", "unauthorized", "forbidden", "token")
JST_FAILURE_MARKERS = ("登录", "未登录", "请登录", "forbidden", "unauthorized")


def _cookie_header(cookies: list[dict[str, Any]]) -> str:
    pairs = []
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _domain_matches(host: str, cookie_domain: str) -> bool:
    domain = (cookie_domain or "").lstrip(".").lower()
    if not domain:
        return False
    host = host.lower()
    return host == domain or host.endswith(f".{domain}")


def _path_matches(request_path: str, cookie_path: str) -> bool:
    path = cookie_path or "/"
    if not request_path.startswith("/"):
        request_path = f"/{request_path}"
    return request_path.startswith(path)


def _cookie_header_for_url(cookies: list[dict[str, Any]], url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    secure = parsed.scheme == "https"
    matched: dict[tuple[str, str, str], str] = {}
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        if not name or value is None:
            continue
        domain = str(cookie.get("domain") or "").strip()
        if not _domain_matches(host, domain):
            continue
        if cookie.get("secure") and not secure:
            continue
        if not _path_matches(path, str(cookie.get("path") or "/")):
            continue
        key = (
            name,
            domain.lstrip(".").lower(),
            str(cookie.get("path") or "/"),
        )
        matched[key] = str(value)

    ordered = sorted(
        matched.items(),
        key=lambda item: (-len(item[0][2]), item[0][1], item[0][0]),
    )
    return "; ".join(f"{name}={value}" for (name, _domain, _path), value in ordered)


def _tmall_json_failure_reason(payload: Any, text: str) -> str | None:
    if not isinstance(payload, dict):
        return None

    success = payload.get("success")
    if success is False:
        return "接口返回 200，但业务结果疑似失败"

    candidates = [
        payload.get("code"),
        payload.get("errorCode"),
        payload.get("subCode"),
        payload.get("retCode"),
        payload.get("status"),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        value = str(candidate).strip()
        lower = value.lower()
        if lower in {"401", "403"}:
            return f"接口返回 200，但业务结果疑似失败：业务码 {value}"
        if any(marker in lower for marker in TMALL_FAILURE_CODE_MARKERS):
            return f"接口返回 200，但业务结果疑似失败：业务码 {value}"

    message_candidates = [
        payload.get("message"),
        payload.get("msg"),
        payload.get("errorMsg"),
        payload.get("subMsg"),
    ]
    for candidate in message_candidates:
        if candidate is None:
            continue
        message = str(candidate).strip()
        lower = message.lower()
        if any(marker.lower() in lower for marker in TMALL_FAILURE_MARKERS):
            return f"接口返回 200，但业务结果疑似失败：{message}"

    lower_text = text.lower()
    if any(marker.lower() in lower_text for marker in TMALL_FAILURE_MARKERS):
        return "接口返回 200，但业务结果疑似失败"
    return None


def _extract_json_payload(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        raise ValueError("响应体为空")
    decoder = JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(stripped[index:])
            return payload
        except json.JSONDecodeError:
            continue
    raise ValueError(f"无法从响应中解析 JSON：{stripped[:300]}")


def _iter_rows(payload: Any):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_rows(item)
        return
    if not isinstance(payload, dict):
        return
    if "o_id" in payload:
        yield payload
    for key in ("rows", "Rows", "data", "Data", "datas", "Datas", "items", "Items", "result", "Result"):
        value = payload.get(key)
        if isinstance(value, list):
            for row in value:
                if isinstance(row, dict):
                    yield row
        elif isinstance(value, dict):
            yield from _iter_rows(value)


def _jst_order_list_failure_reason(response_text: str) -> str | None:
    stripped = response_text.strip()
    if not stripped:
        return "接口返回 200，但响应体为空"
    lower = stripped.lower()
    if any(marker.lower() in lower for marker in JST_FAILURE_MARKERS):
        return "接口返回 200，但业务结果疑似失败"
    try:
        payload = _extract_json_payload(stripped)
    except ValueError as exc:
        return f"接口返回 200，但业务结果疑似失败：{exc}"

    if isinstance(payload, dict):
        if payload.get("GotoLogin") is True:
            return "接口返回 200，但业务结果要求重新登录"
        if payload.get("IsSuccess") is False:
            return "接口返回 200，但业务结果疑似失败"

    if isinstance(payload, dict) and isinstance(payload.get("ReturnValue"), str):
        return_value = payload["ReturnValue"].strip()
        if not return_value:
            return "接口返回 200，但 ReturnValue 为空"
        try:
            payload = json.loads(return_value)
        except json.JSONDecodeError:
            return "接口返回 200，但 ReturnValue 不是有效 JSON"

    if isinstance(payload, dict) and not payload:
        return "接口返回 200，但业务结果为空对象"
    if isinstance(payload, list) and not payload:
        return "接口返回 200，但业务结果为空数组"

    rows = list(_iter_rows(payload))
    if rows:
        return None

    if isinstance(payload, dict):
        has_page_keys = any(key in payload for key in ("total", "Total", "total_count", "TotalCount", "page", "Page"))
        page_meta = payload.get("dp")
        if has_page_keys or isinstance(page_meta, dict):
            return None
    return "接口返回 200，但订单列表结果不包含有效数据"


def _jst_product_export_failure_reason(response: Any) -> str | None:
    text = response.text.strip()
    if not text:
        return "接口返回 200，但响应体为空"
    try:
        payload = response.json()
    except ValueError:
        try:
            payload = _extract_json_payload(text)
        except ValueError as exc:
            return f"接口返回 200，但业务结果疑似失败：{exc}"

    if not isinstance(payload, dict):
        return "接口返回 200，但导出接口返回格式异常"

    code = payload.get("code")
    if code != 0:
        return f"接口返回 200，但业务结果疑似失败：业务码 {code}"

    export = payload.get("data")
    if not isinstance(export, dict):
        return "接口返回 200，但导出接口缺少 data"

    export_url = str(export.get("url") or "").strip()
    if not export_url:
        return "接口返回 200，但导出接口未返回下载地址"
    return None


def _scene_failure_reason(site: str, scene: str, response: Any) -> str | None:
    raw_text = response.text
    if not raw_text.strip():
        return "接口返回 200，但响应体为空"
    if site == "tmall_chaoshi":
        payload = None
        content_type = response.headers.get("Content-Type", "").lower()
        stripped = raw_text.lstrip()
        if "json" in content_type or stripped.startswith("{") or stripped.startswith("["):
            try:
                payload = response.json()
            except ValueError:
                payload = None
        return _tmall_json_failure_reason(payload, raw_text[:3000])
    if site == "jst_erp" and scene == "order_list":
        return _jst_order_list_failure_reason(raw_text)
    if site == "jst_erp" and scene == "product_export":
        return _jst_product_export_failure_reason(response)
    return None


def check_session(site: str, scene: str) -> dict[str, Any]:
    store = SessionStore()
    data = store.load(site, scene)
    if not data:
        raise FileNotFoundError(f"session 不存在：{store.path_for(site, scene)}")
    get_scene_config(site, scene)

    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 requests，请先运行：pip install -r requirements.txt") from exc

    headers = dict(data.get("headers") or {})
    cookies = data.get("cookies") or []
    method = (data.get("method") or "GET").upper()
    url = data.get("url") or ""
    headers = {key: value for key, value in headers.items() if key.lower() != "cookie"}
    cookie_header = _cookie_header_for_url(cookies, url)
    if cookie_header:
        headers["Cookie"] = cookie_header
    post_data = data.get("post_data") or None
    post_data_json = data.get("post_data_json")
    content_type = str(headers.get("Content-Type") or headers.get("content-type") or "").lower()
    result = {
        "status_code": None,
        "valid": False,
        "reason": "",
        "last_check": now_iso(),
    }
    try:
        request_kwargs: dict[str, Any] = {"headers": headers, "timeout": 20}
        if method in {"POST", "PUT", "PATCH"}:
            if isinstance(post_data_json, (dict, list)) and "json" in content_type:
                request_kwargs["json"] = post_data_json
            else:
                request_kwargs["data"] = post_data
        response = requests.request(method, url, **request_kwargs)
        text = response.text[:3000].lower()
        html_like = text.lstrip().startswith("<!doctype html") or text.lstrip().startswith("<html")
        result["status_code"] = response.status_code
        if response.status_code in (401, 403):
            result["reason"] = f"接口返回 {response.status_code}"
        elif html_like and any(marker in text for marker in LOGIN_MARKERS) and "downloadfile" not in text:
            result["reason"] = "疑似返回登录页或权限页"
        elif response.status_code == 200:
            failure_reason = _scene_failure_reason(site, scene, response)
            if failure_reason:
                result["reason"] = failure_reason
            else:
                result["valid"] = True
                result["reason"] = "接口返回 200，session 可用"
        else:
            result["reason"] = f"接口返回 {response.status_code}"
    except Exception as exc:  # requests exceptions vary by installed version
        logging.warning("session 检查失败：%s", exc)
        result["reason"] = f"请求失败：{exc}"

    new_status = "valid" if result["valid"] else "invalid"
    data["status"] = new_status
    data["last_check"] = result
    store.save(site, scene, data)
    return {**data, "check_result": result}
