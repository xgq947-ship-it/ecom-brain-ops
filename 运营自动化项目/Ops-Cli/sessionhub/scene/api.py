from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .session_check import check_session
from .session_store import SessionStore
from .token_capture import CaptureError, capture_session

try:
    from api import save_cookies, save_latest_session
except Exception:  # pragma: no cover - compatibility when imported outside sessionhub root
    save_cookies = None
    save_latest_session = None


class SessionHubError(RuntimeError):
    pass


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


def public_session(data: dict[str, Any]) -> dict[str, Any]:
    headers = dict(data.get("headers") or {})
    cookies = data.get("cookies") or []
    headers = {key: value for key, value in headers.items() if key.lower() != "cookie"}
    cookie_header = _cookie_header_for_url(cookies, str(data.get("url") or ""))
    if cookie_header:
        headers["Cookie"] = cookie_header
    payload = {
        "site": data.get("site"),
        "scene": data.get("scene"),
        "status": data.get("status"),
        "headers": headers,
        "cookies": cookies,
        "tokens": data.get("tokens") or {},
        "url": data.get("url"),
        "method": data.get("method"),
        "post_data": data.get("post_data") or "",
        "post_data_json": data.get("post_data_json"),
        "post_data_form": data.get("post_data_form"),
    }
    if save_cookies and save_latest_session:
        platform = _platform_for_site(str(data.get("site") or ""))
        if platform:
            save_cookies(platform, cookies, source=f"legacy_scene:{data.get('scene')}")
            save_latest_session(
                {
                    "platform": platform,
                    "source": f"legacy_scene:{data.get('scene')}",
                    "url": payload["url"],
                    "method": payload["method"],
                    "headers": headers,
                    "cookies": cookies,
                    "post_data": payload["post_data"],
                    "tokens": payload["tokens"],
                    "legacy_site": data.get("site"),
                    "legacy_scene": data.get("scene"),
                }
            )
    return payload


def _platform_for_site(site: str) -> str:
    mapping = {
        "tmall_chaoshi": "tmall",
        "jst_erp": "jst",
        "tmall": "tmall",
        "jst": "jst",
    }
    return mapping.get(site, "")


def get_session(site: str, scene: str = "download_file_query") -> dict[str, Any]:
    store = SessionStore()
    current = store.load(site, scene)
    if current:
        try:
            checked = check_session(site, scene)
        except Exception as exc:
            raise SessionHubError(f"session 检查失败：{exc}") from exc
        if checked.get("status") == "valid":
            return public_session(checked)
        try:
            capture_session(site, scene)
            checked = check_session(site, scene)
        except CaptureError as exc:
            raise SessionHubError(f"session 无效，准备重新捕获，但重新捕获失败：{exc}") from exc
        except Exception as exc:
            raise SessionHubError(f"session 无效，准备重新捕获；重新捕获后检查失败：{exc}") from exc
        if checked.get("status") == "valid":
            return public_session(checked)
        raise SessionHubError(f"重新捕获后仍检查失败：{checked.get('check_result', {}).get('reason')}")

    try:
        capture_session(site, scene)
        checked = check_session(site, scene)
        if checked.get("status") == "valid":
            return public_session(checked)
        raise SessionHubError(f"重新捕获后仍检查失败：{checked.get('check_result', {}).get('reason')}")
    except CaptureError as exc:
        raise SessionHubError(f"无法获取可用 session：{exc}") from exc
    except Exception as exc:
        raise SessionHubError(f"重新捕获后检查失败：{exc}") from exc
