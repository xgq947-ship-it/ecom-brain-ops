from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin
from urllib.parse import urlparse

from ops_cli.capabilities import current_capability_execution
from ops_cli.config import get_config
from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.utils.http import build_client


TMCS_SITE = "tmall_chaoshi"
TMCS_PRODUCT_SEARCH_SCENE = "maochao_item_search"
TMCS_PRODUCT_EXPORT_SCENE = "maochao_item_export"
TMCS_INVENTORY_SEARCH_SCENE = "maochao_inventory_search"
TMCS_INVENTORY_EXPORT_SCENE = "maochao_inventory_export"
TMCS_INVENTORY_ADJUST_SCENE = "maochao_inventory_adjust"
TMCS_XP_WORKORDER_COUNT_SCENE = "xp_workorder_count"
TMCS_FULFILLMENT_OVERVIEW_SCENE = "fulfillment_overview"
TMCS_MARKETING_RISK_WARNING_SCENE = "marketing_risk_warning_count"
TMCS_BILL_LIST_SCENE = "statement_bill_list_for_supplier"
TMCS_BILL_EXPORT_SCENE = "statement_bill_dynamic_list"
TMCS_BILL_QUERY_SCENE = "download_file_query"
TMCS_BILL_LIST_URL = "https://wdksettlement.hemaos.com/statementBill/v3/listForSupplier"
TMCS_BILL_DOWNLOAD_URL = "https://wdksettlement.hemaos.com/statementBill/downloadFcBillDynamic"
TMCS_PRODUCT_EXPORT_FILENAME = "猫超商品列表导出.xlsx"
TMCS_PRODUCT_LATEST_FILENAME = "猫超商品列表导出 (最新）.xlsx"
TMCS_INVENTORY_EXPORT_FILENAME = "猫超商品库存列表导出.xlsx"
TMCS_STATEMENT_LIST_FILENAME = "对账单列表.xlsx"
NOISY_HEADERS = {
    "accept-encoding",
    "content-length",
    "host",
    "cookie",
}
AUTH_RECOVERY_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "登录",
    "session",
    "cookie",
    "token",
)

# 重放猫超（tmall/havana）接口鉴权必需的 cookie 名单。当 scene/template 落了
# 一整套 *.tmall.com 全量 cookie（几百个、几万字符）时，nginx 会拒为
# 「400 Request Header Or Cookie Too Large」；超阈值时裁到本名单即可恢复。
ESSENTIAL_COOKIE_NAMES = frozenset(
    {
        "cna",
        "cookie2",
        "cookie17",
        "t",
        "sn",
        "unb",
        "sg",
        "sgcookie",
        "csg",
        "csa",
        "_tb_token_",
        "login",
        "cancelledSubSites",
        "SCMLOCALE",
        "locale",
        "ascp_web_canary",
        "SCMSESSID",
        "SCMBIZTYPE",
        "X-XSRF-TOKEN",
        "dnk",
        "tracknick",
        "lid",
        "lgc",
        "wk_cookie2",
        "wk_unb",
        "havana_lgc2_77",
        "_samesite_flag_",
        "uc1",
        "uc3",
        "uc4",
        "xlly_s",
        "_m_h5_tk",
        "_m_h5_tk_enc",
        "isg",
        "tfstk",
    }
)
# nginx large_client_header_buffers 默认每块 8k；留出余量，超过即裁剪。
MAX_COOKIE_HEADER_LEN = 7000


def trim_cookie_header(cookie_header: str | None, *, max_len: int = MAX_COOKIE_HEADER_LEN) -> str:
    """超长 cookie 头裁到鉴权必需项，避免 nginx 400 Header Too Large。

    未超阈值时原样返回；裁剪后若意外为空则回退原值，保证不丢登录态。
    """
    header = str(cookie_header or "")
    if len(header) <= max_len:
        return header
    kept: list[str] = []
    seen: set[str] = set()
    for part in header.split(";"):
        item = part.strip()
        if not item:
            continue
        name = item.split("=", 1)[0]
        if name in ESSENTIAL_COOKIE_NAMES and name not in seen:
            kept.append(item)
            seen.add(name)
    trimmed = "; ".join(kept)
    return trimmed or header


def sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def scene_store_path(site: str, scene: str) -> Path:
    return sessionhub_root() / "data" / "sessions" / site / f"{scene}.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_cookie_header(headers: dict[str, Any], cookies: list[dict[str, Any]] | None) -> dict[str, str]:
    merged = {str(key): str(value) for key, value in headers.items() if str(key).lower() != "cookie"}
    cookie_parts = [
        f"{cookie.get('name')}={cookie.get('value')}"
        for cookie in (cookies or [])
        if cookie.get("name")
    ]
    if cookie_parts:
        merged["cookie"] = "; ".join(cookie_parts)
    return merged


def filter_cookies_for_url(cookies: list[dict[str, Any]] | None, url: str) -> list[dict[str, Any]]:
    if not cookies:
        return []
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return []
    matched: list[dict[str, Any]] = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").strip().lower()
        if not domain:
            continue
        normalized = domain.lstrip(".")
        if host == normalized or host.endswith(f".{normalized}"):
            matched.append(cookie)
    return matched


def sanitize_replay_headers(headers: dict[str, Any], cookies: list[dict[str, Any]] | None = None) -> dict[str, str]:
    merged = merge_cookie_header(headers, cookies)
    return {key: value for key, value in merged.items() if key.lower() not in NOISY_HEADERS}


def is_probable_auth_error(error: object) -> bool:
    text = str(error).strip().lower()
    if not text:
        return False
    return any(marker in text for marker in AUTH_RECOVERY_MARKERS)


def load_scene_or_fail(site: str, scene: str, *, next_command: str) -> dict[str, Any]:
    path = scene_store_path(site, scene)
    if not path.exists():
        raise RuntimeError(f"未找到 scene：{path}。请先运行 `{next_command}`。")
    return read_json(path)


def check_scene_or_fail(site: str, scene: str, *, next_command: str) -> dict[str, Any]:
    manager = get_scene_manager()
    check = manager.check_scene(site, scene)
    if check.get("status") == "valid":
        return check
    execution = current_capability_execution()
    if execution is not None and not execution.allow_recovery:
        execution.recovery.mark_required()
        reason = (check.get("check_result") or {}).get("reason") or "scene 不可用"
        raise RuntimeError(f"Scene 校验失败：{reason}。请先运行 `{next_command}`。")
    try:
        manager.ensure_scene(site, scene)
    except Exception as exc:
        reason = (check.get("check_result") or {}).get("reason") or "scene 不可用"
        raise RuntimeError(f"scene 不可用：{reason}；自动恢复失败：{exc}。请先运行 `{next_command}`。") from exc
    refreshed = manager.check_scene(site, scene)
    if refreshed.get("status") != "valid":
        reason = (refreshed.get("check_result") or {}).get("reason") or "scene 不可用"
        raise RuntimeError(f"scene 不可用：{reason}。请先运行 `{next_command}`。")
    if execution is not None:
        execution.recovery.mark_refreshed(scene)
    return refreshed


def ensure_scene_assets(
    *,
    site: str,
    scene: str,
    force: bool,
    next_command: str,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    manager = get_scene_manager()
    scene_path = scene_store_path(site, scene)
    execution = current_capability_execution()
    if execution is not None and not execution.allow_recovery:
        execution.recovery.mark_required()
        check = manager.check_scene(site, scene)
        if check.get("status") != "valid":
            reason = (check.get("check_result") or {}).get("reason") or "scene 不可用"
            raise RuntimeError(f"Scene 校验失败：{reason}。请先运行 `{next_command}`。")
        return read_json(scene_path), check, scene_path
    if force:
        manager.capture_scene(site, scene)
    else:
        manager.ensure_scene(site, scene)
    check = manager.check_scene(site, scene)
    if check.get("status") != "valid":
        reason = (check.get("check_result") or {}).get("reason") or "scene 不可用"
        raise RuntimeError(f"scene 复检失败：{reason}。请先运行 `{next_command}`。")
    if execution is not None:
        execution.recovery.mark_refreshed(scene)
    return read_json(scene_path), check, scene_path


def parse_form_post_data(post_data: Any, scene: str) -> dict[str, str]:
    if isinstance(post_data, dict):
        return {str(key): str(value) for key, value in post_data.items()}
    if not isinstance(post_data, str) or not post_data.strip():
        raise RuntimeError(f"scene {scene} 缺少表单 post_data")
    form_data = dict(parse_qsl(post_data, keep_blank_values=True))
    if not form_data:
        raise RuntimeError(f"scene {scene} 表单数据为空")
    return form_data


def parse_json_post_data(post_data: Any, scene: str) -> dict[str, Any]:
    if isinstance(post_data, dict):
        return copy.deepcopy(post_data)
    if not isinstance(post_data, str) or not post_data.strip():
        raise RuntimeError(f"scene {scene} 缺少 JSON post_data")
    try:
        payload = json.loads(post_data)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"scene {scene} 的 post_data 不是合法 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"scene {scene} 的 post_data JSON 必须是对象")
    return payload


def is_probably_excel(content: bytes) -> bool:
    return content.startswith(b"PK\x03\x04") or content.startswith(b"\xd0\xcf\x11\xe0")


def normalize_download_url(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith(("http://", "https://")):
        return text
    return None


def find_download_url(value: Any) -> str | None:
    if isinstance(value, str):
        return normalize_download_url(value)
    if isinstance(value, dict):
        preferred_keys = (
            "downloadUrl",
            "downloadURL",
            "downloadLink",
            "fileUrl",
            "fileURL",
            "fileStoreUrl",
            "fileStoreURL",
            "ossUrl",
            "ossURL",
            "url",
            "path",
            "data",
        )
        for key in preferred_keys:
            if key in value:
                found = find_download_url(value[key])
                if found:
                    return found
        for nested in value.values():
            found = find_download_url(nested)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = find_download_url(item)
            if found:
                return found
    return None


def extract_export_task_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        task_id = data.get("taskId") or data.get("id")
        if task_id:
            return str(task_id)
    return None


def gei_task_download_url(export_url: str, task_id: str) -> str:
    return urljoin(export_url, f"/gei/export/task/{task_id}")


def previous_month_range(today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    first_day_this_month = today.replace(day=1)
    last_day_previous_month = first_day_this_month - timedelta(days=1)
    first_day_previous_month = last_day_previous_month.replace(day=1)
    return first_day_previous_month.isoformat(), last_day_previous_month.isoformat()


def unique_path(base: Path) -> Path:
    if not base.exists():
        return base
    counter = 1
    while True:
        candidate = base.with_name(f"{base.stem}({counter}){base.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def build_download_query_payload(template_payload: dict[str, Any], page_index: int = 1, page_size: int = 20) -> dict[str, Any]:
    payload = copy.deepcopy(template_payload)
    parameters = payload.get("parameters")
    if not isinstance(parameters, list) or not parameters or not isinstance(parameters[0], dict):
        payload["parameters"] = [{"pageIndex": page_index, "pageSize": page_size}]
    else:
        parameters[0]["pageIndex"] = page_index
        parameters[0]["pageSize"] = page_size
    return payload


def tmcs_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: Any | None = None,
    json_body: Any | None = None,
    data_body: Any | None = None,
    timeout: float = 120.0,
) -> tuple[int, Any, bytes]:
    with build_client(follow_redirects=True, timeout=timeout) as client:
        response = client.request(method.upper(), url, headers=headers, params=params, json=json_body, content=data_body)
        response.raise_for_status()
        content = response.content
        parsed: Any
        try:
            parsed = response.json()
        except Exception:
            parsed = None
        if parsed is None:
            text = content[:4096].decode("utf-8", errors="ignore")
            if "<title>登录" in text or "登录</title>" in text:
                raise RuntimeError("猫超登录态失效：接口返回登录页面。")
        return response.status_code, parsed, content


def tmcs_download(url: str, *, headers: dict[str, str] | None = None, timeout: float = 120.0) -> tuple[int, Any, bytes]:
    with build_client(follow_redirects=True, timeout=timeout) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        content = response.content
        parsed: Any
        try:
            parsed = response.json()
        except Exception:
            parsed = None
        return response.status_code, parsed, content


def resolve_download_content(
    *,
    content: bytes,
    parsed_payload: Any,
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str | None]:
    if parsed_payload is not None:
        file_url = find_download_url(parsed_payload)
        if file_url:
            _, nested_payload, nested_content = tmcs_download(file_url, headers=headers)
            return resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=headers)
        raise RuntimeError(f"接口返回 JSON，未找到下载地址：{json.dumps(parsed_payload, ensure_ascii=False)[:500]}")
    if not content:
        raise RuntimeError("下载内容为空")
    return content, None


def bill_period_overlaps(bill_code: str, start: date, end: date) -> bool:
    if not bill_code.startswith("HDB") or len(bill_code) < 19:
        return True
    start_text = bill_code[3:11]
    end_text = bill_code[11:19]
    if not (start_text.isdigit() and end_text.isdigit()):
        return True
    bill_start = datetime.strptime(start_text, "%Y%m%d").date()
    bill_end = datetime.strptime(end_text, "%Y%m%d").date()
    return bill_start <= end and bill_end >= start


def iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from iter_dicts(nested)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dicts(item)


def find_success_file_url(value: Any, bill_code: str) -> str | None:
    for row in iter_dicts(value):
        text_blob = json.dumps(row, ensure_ascii=False)
        if bill_code not in text_blob:
            continue
        if row.get("fileStatus") != "SU":
            continue
        found = find_download_url(row)
        if found and bill_code in found:
            return found
    return None


def find_success_file_url_by_keywords(value: Any, keywords: tuple[str, ...]) -> str | None:
    for row in iter_dicts(value):
        if row.get("fileStatus") != "SU":
            continue
        text_blob = json.dumps(row, ensure_ascii=False)
        if not any(keyword in text_blob for keyword in keywords):
            continue
        found = find_download_url(row)
        if found:
            return found
    return None


def form_encode(form_data: dict[str, str]) -> bytes:
    return urlencode(form_data).encode("utf-8")
