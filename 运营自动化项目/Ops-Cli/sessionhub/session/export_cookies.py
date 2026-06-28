#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import load_config, normalize_platform, platform_config, save_cookies  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 9222 专用 Chrome 导出平台 Cookie")
    parser.add_argument("platform", help="tmall 或 jst")
    parser.add_argument("--start-browser", action="store_true", help="CDP 不可用时先启动 Chrome 9222")
    return parser.parse_args()


def ensure_browser(start_browser: bool) -> None:
    if not start_browser:
        return
    script = ROOT / "browser" / "start_chrome_9222.sh"
    subprocess.run([str(script)], check=True)


def domain_allowed(cookie: dict[str, Any], domains: list[str]) -> bool:
    domain = str(cookie.get("domain") or "").lstrip(".").lower()
    return any(domain == item or domain.endswith(f".{item}") for item in domains)


def main() -> int:
    args = parse_args()
    key = normalize_platform(args.platform)
    ensure_browser(args.start_browser)

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        raise SystemExit("缺少 Playwright，请先运行：python3 -m pip install -r sessionhub/requirements.txt") from exc

    config = load_config()
    port = int((config.get("chrome") or {}).get("port") or 9222)
    domains = [str(item).lstrip(".").lower() for item in platform_config(key).get("cookie_domains", [])]

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        cookies = [cookie for cookie in context.cookies() if domain_allowed(cookie, domains)]

    path = save_cookies(key, cookies)
    print(json.dumps({"platform": key, "cookie_count": len(cookies), "saved_to": str(path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
