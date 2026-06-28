from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx

from ops_cli.browser import managed_work_page
from ops_cli.config import get_config
from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.output import CommandResponse
from ops_cli.platforms.jst.shared import ensure_scene_file_ready, surface_jst_login_if_needed
from ops_cli.platforms.jst.shops import default_shop, other_shop_ids, resolve_shop
from ops_cli.runtime_context import write_runtime_context
from ops_cli.utils.http import build_client


JST_SITE = "jst_erp"
BASE_ORDER_SCENE = "order_list"
PROFIT_SCENE = "profit_multi_dimension_report"
TARGET_URL = "https://www.erp321.com/app/order/order/list.aspx"
DEFAULT_EXCLUDED_FLAG = "黄色旗帜"
DEFAULT_EXCLUDED_STATUSES = ["代付款", "已取消"]
DEFAULT_DATE_MODE = "today"
DEFAULT_SUMMARY_FIELD = "已付款金额"
TEMPLATE_PATH = Path("data/jst/order_stats_template.json")
SCENE_MATCH_CONTAINS = [
    "www.erp321.com/app/order/order/list.aspx",
    "LoadDataToJSON",
]
FIELD_CANDIDATES = [
    "已付款金额",
    "paid_amount",
    "pay_amount",
    "pay_amt",
    "payment",
    "payment_amount",
]
ORDER_STATS_PARSE_ATTEMPTS = 3
JST_ORDER_FILTER_IDS = {
    "exclude_flag_checkbox_id": "no_flag_2",
    "date_begin_id": "order_date_begin",
    "date_end_id": "order_date_end",
    "status_include_ids": [
        "status_waitconfirm",
        "status_waitfconfirm",
        "status_delivering",
        "status_sent",
        "status_question",
        "status_waitoutersent",
        "status_merged",
        "status_split",
    ],
    "status_exclude_ids": [
        "status_waitpay",
        "status_cancelled",
    ],
}


class ResponseParseError(RuntimeError):
    def __init__(self, message: str, response_diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.response_diagnostics = response_diagnostics


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _scene_store_path(site: str, scene: str) -> Path:
    return _sessionhub_root() / "data" / "sessions" / site / f"{scene}.json"


def _template_path() -> Path:
    return Path.cwd() / TEMPLATE_PATH


def _today() -> date:
    return date.today()


def _normalize_date(date_value: str) -> date:
    if date_value == DEFAULT_DATE_MODE:
        return _today()
    try:
        return date.fromisoformat(date_value)
    except ValueError as exc:
        raise RuntimeError("日期只支持 today 或 YYYY-MM-DD") from exc


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_cookie_header(headers: dict[str, Any], cookies: list[dict[str, Any]] | None) -> dict[str, str]:
    merged = {str(key): str(value) for key, value in headers.items()}
    if cookies:
        merged["cookie"] = "; ".join(
            f"{cookie.get('name')}={cookie.get('value')}"
            for cookie in cookies
            if cookie.get("name")
        )
    return merged


def _has_cookie_header(headers: dict[str, Any]) -> bool:
    return any(str(key).lower() == "cookie" and str(value).strip() for key, value in headers.items())


def _with_scene_cookies(template: dict[str, Any], scene_path: Path) -> dict[str, Any]:
    headers = dict(template.get("headers") or {})
    if _has_cookie_header(headers) or template.get("cookies"):
        return template
    try:
        cookies = _read_json(scene_path).get("cookies") or []
    except OSError:
        cookies = []
    if not cookies:
        return template
    hydrated = dict(template)
    hydrated["cookies"] = cookies
    return hydrated


def _extract_callback_payload(scene_data: dict[str, Any]) -> dict[str, Any]:
    form = scene_data.get("post_data_form") or {}
    raw = form.get("__CALLBACKPARAM")
    if not raw:
        raise RuntimeError("scene 缺少 __CALLBACKPARAM，无法学习请求模板")
    if isinstance(raw, dict):
        return raw
    return json.loads(str(raw))


def _extract_filters(callback_payload: dict[str, Any]) -> list[dict[str, Any]]:
    args = callback_payload.get("Args") or []
    if len(args) < 2:
        return []
    raw = args[1]
    if not isinstance(raw, str):
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _set_filters(callback_payload: dict[str, Any], filters: list[dict[str, Any]]) -> None:
    args = list(callback_payload.get("Args") or [])
    while len(args) < 2:
        args.append("")
    args[1] = json.dumps(filters, ensure_ascii=False, separators=(",", ":"))
    callback_payload["Args"] = args


def _set_callback_page(callback_payload: dict[str, Any], page: int) -> None:
    args = list(callback_payload.get("Args") or [])
    while len(args) < 1:
        args.append("")
    args[0] = str(page)
    callback_payload["Args"] = args


def _order_date_filter_indices(filters: list[dict[str, Any]]) -> list[int]:
    return [index for index, item in enumerate(filters) if str(item.get("k") or "") == "order_date"]


def _template_date_filter_indices(filters: list[dict[str, Any]], metadata: dict[str, Any]) -> list[int]:
    indices: list[int] = []
    for raw_index in metadata.get("date_filter_indices") or []:
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(filters) and index not in indices:
            indices.append(index)
    for index in _order_date_filter_indices(filters):
        if index not in indices:
            indices.append(index)
    return indices


def _replace_date_like(value: str, base_date: date, target_date: date) -> str:
    base_patterns = {
        base_date.isoformat(): target_date.isoformat(),
        base_date.strftime("%Y/%m/%d"): target_date.strftime("%Y/%m/%d"),
        base_date.strftime("%Y/%-m/%-d"): target_date.strftime("%Y/%-m/%-d"),
        base_date.strftime("%Y-%m-%d 00:00:00"): target_date.strftime("%Y-%m-%d 00:00:00"),
        base_date.strftime("%Y/%m/%d 00:00:00"): target_date.strftime("%Y/%m/%d 00:00:00"),
    }
    result = value
    for source, replacement in base_patterns.items():
        result = result.replace(source, replacement)
    return result


def _infer_template_metadata(
    *,
    filters: list[dict[str, Any]],
    captured_date: date,
    default_store: str,
) -> dict[str, Any]:
    date_indices: list[int] = []
    store_indices: list[int] = []
    flag_indices: list[int] = []
    status_indices: list[int] = []

    for index, item in enumerate(filters):
        value = str(item.get("v") or "")
        if default_store and default_store in value:
            store_indices.append(index)
        if "黄色" in value:
            flag_indices.append(index)
        if any(status in value for status in DEFAULT_EXCLUDED_STATUSES):
            status_indices.append(index)
        if str(item.get("k") or "") == "order_date" or any(
            token in value for token in [captured_date.isoformat(), captured_date.strftime("%Y/%m/%d"), captured_date.strftime("%Y/%-m/%-d")]
        ):
            date_indices.append(index)

    return {
        "captured_for_date": captured_date.isoformat(),
        "default_store": default_store,
        "date_filter_indices": date_indices,
        "store_filter_indices": store_indices,
        "flag_filter_indices": flag_indices,
        "status_filter_indices": status_indices,
    }


def _iter_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        for item in payload:
            rows.extend(_iter_rows(item))
        return rows
    if not isinstance(payload, dict):
        return rows
    row_like_keys = ("o_id", "so_id", "shop_id", "shop_name", "pay_amount")
    if any(key in payload for key in row_like_keys):
        rows.append(payload)
    for key in ("rows", "Rows", "data", "Data", "datas", "Datas", "items", "Items", "result", "Result", "d", "D"):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
        elif isinstance(value, dict):
            rows.extend(_iter_rows(value))
    return rows


def _response_text(source: Any) -> str:
    if isinstance(source, str):
        return source
    return str(getattr(source, "text", "") or "")


def _response_diagnostics(source: Any, text: str) -> dict[str, Any]:
    status_code = getattr(source, "status_code", None)
    headers = getattr(source, "headers", {}) or {}
    raw_length = None
    if hasattr(headers, "get"):
        raw_length = headers.get("content-length") or headers.get("Content-Length")
    try:
        content_length = int(raw_length) if raw_length is not None else len(text.encode("utf-8"))
    except (TypeError, ValueError):
        content_length = len(text.encode("utf-8"))
    return {
        "status_code": status_code,
        "content_length": content_length,
        "response_preview": text.strip()[:300],
    }


def _extract_json_payload(source: Any) -> Any:
    text = _response_text(source)
    stripped = text.strip()
    wrapped = re.match(r"^\d+\|(\{.*\})$", stripped, re.DOTALL)
    if wrapped:
        outer = json.loads(wrapped.group(1))
        return_value = outer.get("ReturnValue")
        if isinstance(return_value, str):
            return json.loads(return_value)
        return outer
    decoder = json.JSONDecoder()
    for index, char in enumerate(stripped):
        if char not in "[{":
            continue
        try:
            payload, _ = decoder.raw_decode(stripped[index:])
            return payload
        except json.JSONDecodeError:
            continue
    raise ResponseParseError(f"无法从响应中解析 JSON：{stripped[:300]}", _response_diagnostics(source, text))


def _extract_numeric(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    text = re.sub(r"[^\d.\-]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _extract_page_size(callback_payload: dict[str, Any]) -> int:
    args = callback_payload.get("Args") or []
    if len(args) < 3:
        return 50
    raw = args[2]
    page_config: Any = raw
    if isinstance(raw, str):
        try:
            page_config = json.loads(raw)
        except json.JSONDecodeError:
            page_config = raw
    if isinstance(page_config, dict):
        for key in ("pageSize", "PageSize", "pagesize"):
            if key in page_config:
                value = _extract_numeric(page_config.get(key))
                if value and value > 0:
                    return int(value)
    value = _extract_numeric(page_config)
    if value and value > 0:
        return int(value)
    return 50


def _summarize_payload(payload: Any) -> tuple[int, float]:
    rows = _iter_rows(payload)
    total = len(rows)
    paid_amount = 0.0
    for row in rows:
        matched = None
        for key in FIELD_CANDIDATES:
            if key in row:
                matched = _extract_numeric(row.get(key))
                if matched is not None:
                    break
        if matched is None:
            for key, value in row.items():
                if "付款" in str(key) or "paid" in str(key).lower():
                    matched = _extract_numeric(value)
                    if matched is not None:
                        break
        if matched is not None:
            paid_amount += matched
    return total, round(paid_amount, 2)


def _scene_is_valid(scene_data: dict[str, Any]) -> dict[str, Any]:
    headers = _merge_cookie_header(
        dict(scene_data.get("headers") or {}),
        scene_data.get("cookies") or [],
    )
    method = str(scene_data.get("method") or "POST").upper()
    url = str(scene_data.get("url") or TARGET_URL)
    post_data = scene_data.get("post_data") or None
    with build_client(follow_redirects=True) as client:
        response = client.request(method, url, headers=headers, data=post_data)
    payload = _extract_json_payload(response)
    total, paid_amount = _summarize_payload(payload)
    return {
        "status_code": response.status_code,
        "order_count": total,
        "paid_amount": paid_amount,
        "valid": response.status_code == 200 and total >= 0,
        "reason": "接口返回 200，scene 可用" if response.status_code == 200 else f"接口返回 {response.status_code}",
    }


def _load_template() -> dict[str, Any]:
    path = _template_path()
    if not path.exists():
        raise RuntimeError(f"未找到订单统计模板：{path}。请先运行 `ops jst order stats learn`。")
    return _read_json(path)


def _write_template(*, scene_data: dict[str, Any], store: str, capture_source: str) -> Path:
    callback_payload = _extract_callback_payload(scene_data)
    filters = _extract_filters(callback_payload)
    metadata = _infer_template_metadata(filters=filters, captured_date=_today(), default_store=store)
    template = {
        "site": JST_SITE,
        "scene": PROFIT_SCENE,
        "capture_source": capture_source,
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "url": scene_data.get("url"),
        "method": scene_data.get("method"),
        "headers": _merge_cookie_header(
            dict(scene_data.get("headers") or {}),
            scene_data.get("cookies") or [],
        ),
        "post_data_form": scene_data.get("post_data_form") or {},
        "callback_payload": callback_payload,
        "metadata": metadata,
        "defaults": {
            "date": DEFAULT_DATE_MODE,
            "store": store,
            "excluded_flag": DEFAULT_EXCLUDED_FLAG,
            "excluded_statuses": DEFAULT_EXCLUDED_STATUSES,
        },
    }
    path = _template_path()
    _write_json(path, template)
    return path


def _apply_template_overrides(
    template: dict[str, Any],
    *,
    date_value: date,
    store: str,
    shop_id: str | None = None,
    page: int | None = None,
) -> tuple[dict[str, str], str, str]:
    headers = _merge_cookie_header(dict(template.get("headers") or {}), template.get("cookies") or [])
    form = dict(template.get("post_data_form") or {})
    callback_payload = json.loads(json.dumps(template.get("callback_payload") or {}))
    filters = _extract_filters(callback_payload)
    metadata = template.get("metadata") or {}
    captured_date = date.fromisoformat(str(metadata.get("captured_for_date") or _today().isoformat()))

    for index in metadata.get("store_filter_indices") or []:
        if 0 <= index < len(filters):
            filters[index]["v"] = store
    # 订单列表接口按 shop_id 过滤（store 名只进响应元数据，不进查询），切店铺必须改这里。
    if shop_id:
        for flt in filters:
            if flt.get("k") == "shop_id":
                flt["v"] = str(shop_id)
    for index in _template_date_filter_indices(filters, metadata):
        filters[index]["v"] = _replace_date_like(str(filters[index].get("v") or ""), captured_date, date_value)

    _set_filters(callback_payload, filters)
    if page is not None:
        _set_callback_page(callback_payload, page)
    form["__CALLBACKPARAM"] = json.dumps(callback_payload, ensure_ascii=False, separators=(",", ":"))
    # 必须 URL 编码：__CALLBACKPARAM 的值是 JSON，含 {}":,[] 及空格等字符，
    # 服务器按 application/x-www-form-urlencoded 解析，不编码会导致解析失败返回空响应。
    encoded = urlencode(form)
    return headers, encoded, json.dumps(callback_payload, ensure_ascii=False, separators=(",", ":"))


def _save_scene_data(payload: dict[str, Any]) -> Path:
    path = _scene_store_path(JST_SITE, PROFIT_SCENE)
    _write_json(path, payload)
    return path


def _apply_filters_to_frame(frame: Any, *, target_date: date) -> None:
    def set_checked(selector: str, checked: bool) -> None:
        frame.locator(selector).evaluate(
            """(el, checked) => {
                el.checked = checked;
                el.dispatchEvent(new Event("change", { bubbles: true }));
                el.dispatchEvent(new Event("click", { bubbles: true }));
            }""",
            checked,
        )

    # 店铺勾选从注册表派生：取消勾选其它已注册店铺，只勾选默认店铺。
    for shop_id in other_shop_ids():
        try:
            set_checked(f"#shop_{shop_id}", False)
        except Exception:
            pass
    set_checked(f"#shop_{default_shop().shop_id}", True)
    set_checked(f"#{JST_ORDER_FILTER_IDS['exclude_flag_checkbox_id']}", True)
    frame.locator(f"#{JST_ORDER_FILTER_IDS['date_begin_id']}").fill(target_date.isoformat(), timeout=5000)
    frame.locator(f"#{JST_ORDER_FILTER_IDS['date_end_id']}").fill(target_date.isoformat(), timeout=5000)
    for status_id in JST_ORDER_FILTER_IDS["status_include_ids"]:
        try:
            set_checked(f"#{status_id}", True)
        except Exception:
            pass
    for status_id in JST_ORDER_FILTER_IDS["status_exclude_ids"]:
        try:
            set_checked(f"#{status_id}", False)
        except Exception:
            pass


def _capture_profit_scene(*, store: str) -> dict[str, Any]:
    session = get_scene_manager().ensure_scene(JST_SITE, BASE_ORDER_SCENE)
    root = _sessionhub_root()
    import sys

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
    deadline_seconds = 90

    def on_request(request: Any) -> None:
        nonlocal captured
        if captured is not None:
            return
        if request.method.upper() != "POST":
            return
        if not all(part in request.url for part in SCENE_MATCH_CONTAINS):
            return
        post_data = request.post_data or ""
        if "LoadDataToJSON" not in post_data and "LoadDataToJSONPage" not in post_data:
            return
        parsed_form = {key: values[0] if len(values) == 1 else values for key, values in parse_qs(post_data, keep_blank_values=True).items()}
        callback_raw = parsed_form.get("__CALLBACKPARAM")
        if isinstance(callback_raw, str):
            try:
                callback_payload = json.loads(callback_raw)
                args = callback_payload.get("Args") or []
                if len(args) >= 2 and args[1] == "[]":
                    return
            except Exception:
                pass
        captured = {
            "site": JST_SITE,
            "scene": PROFIT_SCENE,
            "status": "captured",
            "source": "sessionhub_9222",
            "url": request.url,
            "method": request.method.upper(),
            "headers": dict(request.headers),
            "post_data": post_data,
            "post_data_json": None,
            "post_data_form": parsed_form,
            "cookies": [],
            "tokens": {},
            "meta": {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "target_url": TARGET_URL,
                "discovery_strategy": "sessionhub_9222_with_primary_probe_reference",
                "default_store": store,
            },
        }

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        with managed_work_page(context, "jst.order.stats") as page:
            context.on("request", on_request)
            try:
                page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                pass

            surface_jst_login_if_needed(page)
            page.wait_for_timeout(2500)
            frame = page.frame_locator("#s_filter_frame")
            frame.locator("body").inner_text(timeout=20000)
            _apply_filters_to_frame(frame, target_date=_today())
            try:
                frame.locator("text=组合查询").first.click(timeout=5000)
            except Exception:
                pass
            deadline = datetime.now().timestamp() + deadline_seconds
            while captured is None and datetime.now().timestamp() < deadline:
                try:
                    frame.locator("text=组合查询").first.click(timeout=1500)
                except Exception:
                    pass
                page.wait_for_timeout(1000)

            if captured is None:
                raise RuntimeError("未捕获到订单统计请求。请确认 9222 Chrome 已登录聚水潭，并且页面已能自动触发组合查询。")
            try:
                captured["cookies"] = context.cookies([captured["url"]])
            except Exception:
                captured["cookies"] = context.cookies()

    _save_scene_data(captured)
    return captured


def learn_order_stats(*, force: bool = False) -> CommandResponse:
    store = get_config().jst_order_stats_store
    scene_path = _scene_store_path(JST_SITE, PROFIT_SCENE)
    inputs = {"site": JST_SITE, "scene": PROFIT_SCENE, "force": force, "store": store}

    if scene_path.exists() and not force:
        scene_data = _read_json(scene_path)
        try:
            check = _scene_is_valid(scene_data)
            if check["valid"]:
                template_path = _write_template(scene_data=scene_data, store=store, capture_source="existing_scene")
                context_path = write_runtime_context(
                    task_name="jst_order_stats_learn",
                    status="success",
                    inputs=inputs,
                    outputs={"scene_path": str(scene_path), "template_path": str(template_path), "reuse": True},
                    artifacts=[str(scene_path), str(template_path)],
                )
                return CommandResponse(
                    success=True,
                    platform="jst",
                    command="order stats learn",
                    data={
                        "site": JST_SITE,
                        "scene": PROFIT_SCENE,
                        "source": "existing_scene",
                        "scene_path": str(scene_path),
                        "template_path": str(template_path),
                        "context_path": str(context_path),
                        "next_command": "ops --json jst order stats",
                    },
                )
        except Exception:
            pass

    captured = _capture_profit_scene(store=store)
    template_path = _write_template(scene_data=captured, store=store, capture_source="sessionhub_9222")
    check = _scene_is_valid(captured)
    context_path = write_runtime_context(
        task_name="jst_order_stats_learn",
        status="success" if check["valid"] else "failed",
        inputs=inputs,
        outputs={"scene_path": str(scene_path), "template_path": str(template_path), "check": check},
        artifacts=[str(scene_path), str(template_path)],
    )
    if not check["valid"]:
        raise RuntimeError(f"scene 已捕获，但复检失败：{check['reason']}")
    return CommandResponse(
        success=True,
        platform="jst",
        command="order stats learn",
        data={
            "site": JST_SITE,
            "scene": PROFIT_SCENE,
            "source": "sessionhub_9222",
            "scene_path": str(scene_path),
            "template_path": str(template_path),
            "context_path": str(context_path),
            "next_command": "ops --json jst order stats",
            "probe_note": "前期主力 Chrome 探测口径已预留到 scene metadata，长期执行以 9222 SessionHub 结果为准",
        },
    )


def run_order_stats(
    *, date_arg: str = DEFAULT_DATE_MODE, store: str | None = None, shop: str | None = None
) -> CommandResponse:
    selected_date = _normalize_date(date_arg)
    # 优先级：--store 显式覆盖 > --shop 注册表 > 配置默认店铺。
    # shop_id 决定真实查询过滤；store 名只进响应元数据。
    if store:
        selected_store = store
        try:
            selected_shop_id = resolve_shop(store).shop_id
        except RuntimeError:
            selected_shop_id = None  # --store 用了未注册的名字，保留模板默认 shop_id
    elif shop:
        resolved = resolve_shop(shop)
        selected_store = resolved.shop_name
        selected_shop_id = resolved.shop_id
    else:
        default = default_shop()
        selected_store = get_config().jst_order_stats_store or default.shop_name
        selected_shop_id = default.shop_id
    template = _load_template()
    scene_path = _scene_store_path(JST_SITE, PROFIT_SCENE)
    ensure_scene_file_ready(
        scene_path=scene_path,
        read_scene=_read_json,
        validate_scene=_scene_is_valid,
        refresh_scene=learn_order_stats,
        next_command="ops jst order stats learn",
        missing_label="订单统计 scene",
        invalid_label="订单统计 scene",
    )
    template = _with_scene_cookies(template, scene_path)

    _, _, first_callback_payload = _apply_template_overrides(
        template, date_value=selected_date, store=selected_store, shop_id=selected_shop_id
    )
    page_size = _extract_page_size(json.loads(first_callback_payload))
    method = str(template.get("method") or "POST").upper()
    url = str(template.get("url") or TARGET_URL)
    all_rows: list[dict[str, Any]] = []
    status_code = 0
    with build_client(follow_redirects=True) as client:
        page = 1
        while True:
            headers, post_data, callback_payload = _apply_template_overrides(
                template,
                date_value=selected_date,
                store=selected_store,
                shop_id=selected_shop_id,
                page=page,
            )
            parse_error: ResponseParseError | None = None
            for _ in range(ORDER_STATS_PARSE_ATTEMPTS):
                response = client.request(method, url, headers=headers, data=post_data)
                status_code = response.status_code
                try:
                    payload = _extract_json_payload(response)
                    break
                except ResponseParseError as exc:
                    parse_error = exc
            else:
                assert parse_error is not None
                raise parse_error
            rows = _iter_rows(payload)
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            page += 1
    order_count, paid_amount = _summarize_payload({"rows": all_rows})
    context_path = write_runtime_context(
        task_name="jst_order_stats_run",
        status="success",
        inputs={"date": selected_date.isoformat(), "store": selected_store, "scene": PROFIT_SCENE},
        outputs={"order_count": order_count, "paid_amount": paid_amount, "status_code": status_code},
    )
    return CommandResponse(
        success=True,
        platform="jst",
        command="order stats",
        data={
            "date": selected_date.isoformat(),
            "store": selected_store,
            "excluded_flag": DEFAULT_EXCLUDED_FLAG,
            "excluded_statuses": DEFAULT_EXCLUDED_STATUSES,
            "order_count": order_count,
            "paid_amount": paid_amount,
            "metric_field": DEFAULT_SUMMARY_FIELD,
            "scene": PROFIT_SCENE,
            "source": "sessionhub",
            "context_path": str(context_path),
            "request": {"url": url, "method": method, "callback": first_callback_payload},
        },
    )
