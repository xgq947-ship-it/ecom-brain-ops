#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from time import sleep
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    import yaml  # type: ignore
except ModuleNotFoundError:
    yaml = None


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import REQUEST_DIR, load_config, normalize_platform, now_iso  # noqa: E402


RULES_PATH = ROOT / "capture" / "filter_rules.yaml"
LOG_DIR = ROOT / "logs"
LOG_PATH = LOG_DIR / "capture.log"
SENSITIVE_HEADERS = {"cookie", "authorization", "token", "x-csrf-token", "x-xsrf-token"}
REDACTED = "***REDACTED***"
DOMAIN_PLATFORM_MAP = {
    "erp321.com": "jst",
    "jushuitan.com": "jst",
    "tmall.com": "tmall",
    "taobao.com": "tmall",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="捕获 9222 专用 Chrome 的接口请求")
    parser.add_argument("platform", nargs="?", default="auto", help="tmall、jst 或 auto；不传则只监听现有 9222 Chrome")
    parser.add_argument("--url", help="打开目标页面；不传则打开 platform home_url")
    parser.add_argument("--wait", type=int, default=90, help="监听秒数")
    parser.add_argument("--contains", action="append", default=[], help="URL 必须包含的片段，可重复")
    return parser.parse_args()


def load_rules() -> dict[str, Any]:
    if not RULES_PATH.exists() or yaml is None:
        return {
            "allowed_domains": ["erp321.com", "jushuitan.com", "tmall.com", "taobao.com"],
            "blocked_resource_types": ["image", "font", "stylesheet", "media"],
            "record_methods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
        }
    return yaml.safe_load(RULES_PATH.read_text(encoding="utf-8")) or {}


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def domain_allowed(url: str, allowed_domains: list[str]) -> bool:
    host = urlparse(url).hostname or ""
    host = host.lower()
    return any(host == item or host.endswith(f".{item}") for item in allowed_domains)


def platform_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for domain, platform in DOMAIN_PLATFORM_MAP.items():
        if host == domain or host.endswith(f".{domain}"):
            return platform
    return "unknown"


def safe_headers(headers: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            result[key] = REDACTED
        else:
            result[key] = value
    return result


def query_dict(url: str) -> dict[str, Any]:
    parsed = parse_qs(urlparse(url).query, keep_blank_values=True)
    return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}


def request_body(request: Any) -> Any:
    raw = request.post_data or ""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        parsed = parse_qs(raw, keep_blank_values=True)
        if parsed:
            return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
    return {"raw": raw[:3000]}


def response_preview(response: Any, content_type: str) -> str:
    try:
        if "json" in content_type or "text" in content_type or "javascript" in content_type:
            return response.text()[:3000]
    except Exception as exc:  # noqa: BLE001 - capture preview should not break capture.
        logging.warning("response preview failed: %s", exc)
    return ""


def request_record(request: Any, selected_platform: str, response: Any | None = None) -> dict[str, Any]:
    parsed = urlparse(request.url)
    detected_platform = platform_from_url(request.url)
    platform = detected_platform if detected_platform != "unknown" else selected_platform
    headers = safe_headers(dict(request.headers))
    content_type = ""
    status = None
    preview = ""
    if response is not None:
        status = response.status
        content_type = response.headers.get("content-type", "")
        preview = response_preview(response, content_type)
    return {
        "captured_at": now_iso(),
        "platform": platform,
        "method": request.method,
        "url": request.url,
        "path": parsed.path,
        "query": query_dict(request.url),
        "headers": headers,
        "request_body": request_body(request),
        "status": status,
        "response_preview": preview,
        "content_type": content_type,
        "source": "chrome_9222",
    }


def main() -> int:
    setup_logging()
    args = parse_args()
    platform = "auto" if args.platform == "auto" else normalize_platform(args.platform)
    config = load_config()
    platform_cfg = {} if platform == "auto" else (config.get("platforms") or {}).get(platform) or {}
    target_url = args.url or platform_cfg.get("home_url")
    if platform != "auto" and not target_url:
        raise SystemExit(f"{platform} 未配置 home_url，也未传 --url")

    rules = load_rules()
    allowed_domains = [str(item).lstrip(".").lower() for item in rules.get("allowed_domains", [])]
    blocked_types = set(rules.get("blocked_resource_types", []))
    record_methods = {str(item).upper() for item in rules.get("record_methods", [])}
    port = int((config.get("chrome") or {}).get("port") or 9222)
    requests_path = REQUEST_DIR / "requests.jsonl"
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit("缺少 Playwright，请先运行：python3 -m pip install -r sessionhub/requirements.txt") from exc

    captured_count = 0
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        def should_capture(request: Any) -> bool:
            if request.method.upper() not in record_methods:
                return False
            if request.resource_type in blocked_types:
                return False
            if args.contains and not all(part in request.url for part in args.contains):
                return False
            return domain_allowed(request.url, allowed_domains)

        def write_record(record: dict[str, Any]) -> None:
            nonlocal captured_count
            try:
                with requests_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                captured_count += 1
            except Exception:  # noqa: BLE001 - capture must keep running.
                logging.exception("write capture record failed")

        def on_request(request: Any) -> None:
            try:
                if should_capture(request):
                    logging.info("matched request: %s %s", request.method, request.url)
            except Exception:  # noqa: BLE001 - event handler must not break capture.
                logging.exception("request capture failed")

        def on_response(response: Any) -> None:
            try:
                request = response.request
                if should_capture(request):
                    write_record(request_record(request, platform, response))
            except Exception:  # noqa: BLE001 - event handler must not break capture.
                logging.exception("response capture failed")

        def on_request_failed(request: Any) -> None:
            try:
                if should_capture(request):
                    write_record(request_record(request, platform))
            except Exception:  # noqa: BLE001 - event handler must not break capture.
                logging.exception("failed-request capture failed")

        context.on("request", on_request)
        context.on("response", on_response)
        context.on("requestfailed", on_request_failed)
        page = context.pages[0] if context.pages else context.new_page()
        if target_url:
            page.goto(str(target_url), wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(args.wait * 1000)
        else:
            sleep(args.wait)

    print(json.dumps({"platform": platform, "captured": captured_count, "requests_path": str(requests_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
