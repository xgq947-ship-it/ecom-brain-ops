from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from .chrome_cdp import CDP_URL, bring_chrome_to_front, hide_chrome, start_chrome
from .session_store import SessionStore
from .site_config import load_site_config, target_url_for


def _progress(message: str) -> None:
    print(message, file=sys.stderr)


class CaptureError(RuntimeError):
    pass


@contextmanager
def _managed_capture_page(context: Any, owner: str):
    try:
        from ops_cli.browser import managed_work_page  # type: ignore
    except Exception:
        page = context.new_page()
        try:
            yield page
        finally:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
        return

    with managed_work_page(context, owner) as page:
        yield page


NOISY_HEADERS = {
    "accept-encoding",
    "content-length",
    "host",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "sec-fetch-user",
    "upgrade-insecure-requests",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in NOISY_HEADERS}


def _parse_token(url: str, headers: dict[str, str], post_data: str | None) -> str:
    query = parse_qs(urlparse(url).query)
    if query.get("_scm_token_"):
        return query["_scm_token_"][0]
    for key, value in headers.items():
        if key.lower() == "_scm_token_":
            return value
    if post_data:
        body_query = parse_qs(post_data)
        if body_query.get("_scm_token_"):
            return body_query["_scm_token_"][0]
        try:
            body_json = json.loads(post_data)
        except json.JSONDecodeError:
            body_json = None
        if isinstance(body_json, dict) and body_json.get("_scm_token_"):
            return str(body_json["_scm_token_"])
    return ""


def _json_body(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _form_body(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    parsed = parse_qs(raw, keep_blank_values=True)
    if not parsed:
        return None
    return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}


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


def _connect_over_cdp(sync_playwright_obj: Any, playwright_error: type[Exception]):
    try:
        return sync_playwright_obj.chromium.connect_over_cdp(CDP_URL)
    except playwright_error:
        logging.exception("连接现有 Chrome CDP 失败，准备重启专用 Chrome 后重试")
        ok, msg = start_chrome(force=True)
        if not ok:
            raise CaptureError(f"连接 Chrome CDP 失败，且无法自动重启专用 Chrome：{msg}")
        _progress(f"{msg}，已自动重启专用 Chrome")
        return sync_playwright_obj.chromium.connect_over_cdp(CDP_URL)


def _is_login_page(current_url: str, login_url: str) -> bool:
    if not current_url:
        return False
    current = urlparse(current_url)
    login = urlparse(login_url)
    if login.scheme and login.netloc and current.netloc == login.netloc:
        login_path = login.path or "/"
        if "login" in login_path.lower():
            return (current.path or "/").startswith(login_path)
    text = current_url.lower()
    return "/login" in text or "login?" in text


def _click_by_text(page: Any, text: str, *, wait_ms: int = 0) -> bool:
    candidates = [
        page.get_by_role("button", name=text, exact=True),
        page.get_by_role("button", name=text),
        page.locator(f"text={text}"),
    ]
    for locator in candidates:
        try:
            target = locator.first
            if wait_ms > 0:
                # SPA pages render controls asynchronously; wait for the element
                # to appear instead of giving up on the first synchronous check.
                target.wait_for(state="visible", timeout=wait_ms)
            elif target.count() <= 0:
                continue
            target.click(timeout=2000)
            return True
        except Exception:
            continue
    return False


def _click_any_text(page: Any, texts: list[str], *, wait_ms: int = 0) -> bool:
    # First pass: cheap synchronous probe so an already-rendered control is
    # clicked immediately without paying the per-text wait timeout.
    for text in texts:
        if _click_by_text(page, text):
            return True
    if wait_ms <= 0:
        return False
    # Second pass: wait for late-rendering controls (SPA), sharing the budget
    # across candidates so a single slow text does not consume the whole window.
    per_text_wait = max(int(wait_ms / max(len(texts), 1)), 500)
    for text in texts:
        if _click_by_text(page, text, wait_ms=per_text_wait):
            return True
    return False


def _run_auto_actions(page: Any, actions: list[dict[str, Any]], target_url: str, *, click_wait_ms: int = 0) -> None:
    for action in actions:
        action_type = str(action.get("type") or "").strip().lower()
        if action_type == "goto_target":
            current_url = getattr(page, "url", "") or ""
            # Use prefix matching so SPA hash/query routing doesn't trigger a redundant navigation
            target_base = target_url.rstrip("/").split("?")[0].split("#")[0]
            current_base = current_url.split("?")[0].split("#")[0].rstrip("/")
            already_there = current_base == target_base or current_base.startswith(target_base + "/")
            if not already_there:
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    continue
        elif action_type == "reload":
            try:
                page.reload(wait_until="domcontentloaded", timeout=10000)
            except Exception:
                continue
        elif action_type == "click_text":
            text = str(action.get("text") or "").strip()
            if not text:
                continue
            _click_by_text(page, text, wait_ms=click_wait_ms)
        elif action_type == "click_any_text":
            texts = [str(text).strip() for text in action.get("texts") or [] if str(text).strip()]
            if not texts:
                continue
            _click_any_text(page, texts, wait_ms=click_wait_ms)


def capture_session(site: str, scene: str, wait_seconds: int = 90) -> dict[str, Any]:
    ok, msg = start_chrome()
    if not ok:
        logging.error("CDP 连接失败：%s", msg)
        raise CaptureError(f"{msg}\n无法自动启动专用 Chrome，请检查 Google Chrome 是否已安装。")
    _progress(msg)

    config = load_site_config(site)
    scene_config = (config.get("scenes") or {}).get(scene)
    if not scene_config:
        raise CaptureError(f"{site} 未配置场景：{scene}")
    target_url = target_url_for(config, scene_config)
    contains = scene_config.get("match_url_contains") or []
    expected_method = (scene_config.get("method") or "").upper()
    auto_actions = list(scene_config.get("auto_actions") or [])
    effective_wait_seconds = int(scene_config.get("wait_seconds") or wait_seconds)
    capture_retry_limit = max(int(scene_config.get("capture_retry_limit") or 1), 1)
    # Seconds between auto-action retries; SPA scenes can lower this to poll faster.
    action_interval = max(float(scene_config.get("action_interval_seconds") or 5.0), 0.5)
    # When >0, clicks wait for late-rendering controls (SPA) instead of probing once.
    click_wait_ms = max(int(scene_config.get("click_wait_ms") or 0), 0)
    login_url = str(config.get("login_url") or "")
    captured: dict[str, Any] | None = None

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise CaptureError("缺少 Playwright，请先运行：pip install -r requirements.txt") from exc

    def on_request(request: Any) -> None:
        nonlocal captured
        if captured is not None:
            return
        url = request.url
        method = request.method.upper()
        if expected_method and method != expected_method:
            return
        if not all(part in url for part in contains):
            return
        headers = _clean_headers(dict(request.headers))
        post_data = request.post_data
        captured = {
            "site": site,
            "scene": scene,
            "status": "captured",
            "source": "chrome_cdp",
            "url": url,
            "method": method,
            "headers": headers,
            "post_data": post_data or "",
            "post_data_json": _json_body(post_data),
            "post_data_form": _form_body(post_data),
            "cookies": [],
            "tokens": {"_scm_token_": _parse_token(url, headers, post_data)},
            "last_check": None,
            "meta": {"captured_at": _now(), "target_url": target_url},
        }

    with sync_playwright() as p:
        browser = _connect_over_cdp(p, PlaywrightError)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        # Auto-recovery should stay in the background unless we explicitly detect
        # that the user must handle login or another manual page action.
        hide_chrome()
        with _managed_capture_page(context, f"sessionhub.{site}.{scene}") as page:
            context.on("request", on_request)
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                logging.warning("页面打开超时，继续等待用户手动刷新触发请求：%s", target_url)
            current_url = getattr(page, "url", "") or ""
            if _is_login_page(current_url, login_url):
                bring_chrome_to_front()
                _progress("检测到登录页，已将专用 Chrome 切到前台，请先完成登录。")
            else:
                hide_chrome()
            _progress("已打开专用 Chrome 目标页面。")
            _progress("如果页面要求登录，请直接在专用 Chrome 里完成登录。")
            if auto_actions:
                _progress("登录后脚本会自动刷新页面并执行固定动作触发目标接口。")
            else:
                _progress("登录后请刷新目标页面，或点击查询/搜索/翻页触发目标接口。")
            _progress(f"正在监听 {effective_wait_seconds} 秒，匹配 URL 包含：{', '.join(contains)}")
            deadline = time.monotonic() + effective_wait_seconds
            next_action_at = time.monotonic() + 1.0
            action_attempts = 0
            try:
                while captured is None and time.monotonic() < deadline:
                    current_url = getattr(page, "url", "") or ""
                    if (
                        auto_actions
                        and action_attempts < capture_retry_limit
                        and time.monotonic() >= next_action_at
                        and not _is_login_page(current_url, login_url)
                    ):
                        _run_auto_actions(page, auto_actions, target_url, click_wait_ms=click_wait_ms)
                        action_attempts += 1
                        next_action_at = time.monotonic() + action_interval
                    page.wait_for_timeout(500)
            except PlaywrightError as exc:
                logging.exception("捕获过程中 Chrome 页面或 CDP 连接断开")
                raise CaptureError("捕获过程中 Chrome 页面被关闭或连接断开。请重新运行命令，脚本会再次弹出专用 Chrome。") from exc
            if captured is None:
                logging.error("捕获失败：%s %s", site, scene)
                raise CaptureError("未捕获到匹配请求。请在弹出的 Chrome 里确认已登录目标页面，再刷新或点击查询/搜索/翻页后重试。")
            captured["cookies"] = context.cookies()
            cookie_header = _cookie_header_for_url(captured["cookies"], captured["url"])
            captured["headers"] = {
                key: value for key, value in captured["headers"].items() if key.lower() != "cookie"
            }
            if cookie_header:
                captured["headers"]["Cookie"] = cookie_header

    path = SessionStore().save(site, scene, captured)
    captured["saved_to"] = str(path)
    return captured
