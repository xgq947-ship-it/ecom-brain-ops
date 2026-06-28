from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import yaml  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    yaml = None


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
COOKIE_DIR = ROOT / "data" / "cookies"
SESSION_DIR = ROOT / "data" / "sessions"
REQUEST_DIR = ROOT / "data" / "requests"
LATEST_SESSION_PATH = SESSION_DIR / "latest_session.json"


class SessionHubError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SessionHubError(f"找不到 SessionHub 配置：{CONFIG_PATH}")
    text = CONFIG_PATH.read_text(encoding="utf-8")
    if yaml is None:
        return _fallback_config()
    return yaml.safe_load(text) or {}


def _fallback_config() -> dict[str, Any]:
    return {
        "chrome": {
            "port": 9222,
            "user_data_dir": "~/.sessionhub/chrome-9222",
            "app_path": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        },
        "platforms": {
            "tmall": {
                "aliases": ["tmall", "tmall_chaoshi", "maochao"],
                "home_url": "https://web.txcs.tmall.com/",
                "login_check_url": "https://web.txcs.tmall.com/",
                "cookie_domains": ["tmall.com", "taobao.com", "hemaos.com"],
            },
            "jst": {
                "aliases": ["jst", "jst_erp", "jushuitan"],
                "home_url": "https://www.erp321.com/app/order/order/list.aspx",
                "login_check_url": "https://www.erp321.com/app/order/order/list.aspx",
                "cookie_domains": ["erp321.com", "jushuitan.com"],
            },
        },
        "capture": {
            "allowed_domains": ["erp321.com", "jushuitan.com", "tmall.com", "taobao.com"],
        },
    }


def normalize_platform(platform: str) -> str:
    raw = platform.strip().lower()
    config = load_config()
    for key, value in (config.get("platforms") or {}).items():
        aliases = [key, *list(value.get("aliases") or [])]
        if raw in {str(alias).lower() for alias in aliases}:
            return str(key)
    raise SessionHubError(f"未知 platform：{platform}")


def platform_config(platform: str) -> dict[str, Any]:
    key = normalize_platform(platform)
    config = load_config()
    return dict((config.get("platforms") or {}).get(key) or {})


def _domain_matches(host: str, cookie_domain: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    host = host.lower()
    return host == domain or host.endswith(f".{domain}")


def cookie_header_for_url(cookies: list[dict[str, Any]], url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    secure = parsed.scheme == "https"
    pairs: dict[tuple[str, str, str], str] = {}
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = cookie.get("value")
        domain = str(cookie.get("domain") or "").strip()
        cookie_path = str(cookie.get("path") or "/")
        if not name or value is None or not domain:
            continue
        if not _domain_matches(host, domain):
            continue
        if cookie.get("secure") and not secure:
            continue
        if not path.startswith(cookie_path):
            continue
        pairs[(name, domain.lstrip(".").lower(), cookie_path)] = str(value)
    ordered = sorted(pairs.items(), key=lambda item: (-len(item[0][2]), item[0][1], item[0][0]))
    return "; ".join(f"{name}={value}" for (name, _domain, _path), value in ordered)


def cookie_file(platform: str) -> Path:
    return COOKIE_DIR / f"{normalize_platform(platform)}.json"


def load_cookies(platform: str) -> list[dict[str, Any]]:
    path = cookie_file(platform)
    if not path.exists():
        raise SessionHubError(f"Cookie 文件不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        cookies = payload.get("cookies")
    else:
        cookies = payload
    if not isinstance(cookies, list):
        raise SessionHubError(f"Cookie 文件格式不正确：{path}")
    return cookies


def save_cookies(platform: str, cookies: list[dict[str, Any]], source: str = "chrome_9222") -> Path:
    key = normalize_platform(platform)
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "platform": key,
        "source": source,
        "updated_at": now_iso(),
        "cookies": cookies,
    }
    path = cookie_file(key)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def check_login(platform: str) -> dict[str, Any]:
    key = normalize_platform(platform)
    cfg = platform_config(key)
    url = str(cfg.get("login_check_url") or cfg.get("home_url") or "").strip()
    if not url:
        raise SessionHubError(f"{key} 未配置 login_check_url")
    try:
        import requests
    except ModuleNotFoundError as exc:
        raise SessionHubError("缺少 requests，请先安装 sessionhub/requirements.txt") from exc
    cookies = load_cookies(key)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/147 Safari/537.36",
        "Cookie": cookie_header_for_url(cookies, url),
    }
    response = requests.get(url, headers=headers, timeout=20)
    text = response.text[:3000].lower()
    invalid_markers = ("login", "登录", "passport", "unauthorized", "forbidden")
    valid = response.status_code == 200 and not any(marker in text for marker in invalid_markers)
    return {
        "platform": key,
        "valid": valid,
        "status_code": response.status_code,
        "reason": "登录态可用" if valid else "疑似未登录或权限失效",
        "checked_at": now_iso(),
    }


def save_latest_session(session: dict[str, Any]) -> Path:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**session, "updated_at": now_iso()}
    LATEST_SESSION_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return LATEST_SESSION_PATH


def get_session(platform: str) -> dict[str, Any]:
    key = normalize_platform(platform)
    if not LATEST_SESSION_PATH.exists():
        cookies = load_cookies(key)
        cfg = platform_config(key)
        session = {
            "platform": key,
            "source": "cookies",
            "url": cfg.get("home_url", ""),
            "method": "GET",
            "headers": {"Cookie": cookie_header_for_url(cookies, str(cfg.get("home_url") or ""))},
            "cookies": cookies,
        }
        save_latest_session(session)
        return session
    session = json.loads(LATEST_SESSION_PATH.read_text(encoding="utf-8"))
    if session.get("platform") != key:
        cookies = load_cookies(key)
        cfg = platform_config(key)
        return {
            "platform": key,
            "source": "cookies",
            "url": cfg.get("home_url", ""),
            "method": "GET",
            "headers": {"Cookie": cookie_header_for_url(cookies, str(cfg.get("home_url") or ""))},
            "cookies": cookies,
        }
    return session
