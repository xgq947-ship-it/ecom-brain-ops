#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import check_login  # noqa: E402

SESSION_DIR = ROOT / "data" / "sessions"
LOG_DIR = ROOT / "logs"
HEALTH_STATUS_PATH = SESSION_DIR / "health_status.json"
LOG_PATH = LOG_DIR / "check_login.log"
PLATFORMS = ("tmall", "jst")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查平台登录态")
    parser.add_argument("platform", choices=[*PLATFORMS, "all"], help="tmall、jst 或 all")
    return parser.parse_args()


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def check_platform(platform: str) -> dict[str, str]:
    checked_at = now_text()
    try:
        result = check_login(platform)
        valid = bool(result.get("valid"))
        status = "valid" if valid else "expired"
        message = "login valid" if valid else "login expired or redirect to login page"
    except Exception as exc:  # noqa: BLE001 - health check must keep checking other platforms.
        status = "expired"
        message = f"login check failed: {exc}"
        logging.exception("%s login check failed", platform)
    else:
        logging.info("%s login status: %s", platform, status)
    return {
        "status": status,
        "checked_at": checked_at,
        "message": message,
    }


def save_health_status(results: dict[str, dict[str, str]]) -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": now_text(),
        "platforms": results,
    }
    HEALTH_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    setup_logging()
    args = parse_args()
    platforms = PLATFORMS if args.platform == "all" else (args.platform,)
    results = {platform: check_platform(platform) for platform in platforms}
    save_health_status(results)

    has_expired = False
    for platform in platforms:
        status = results[platform]["status"]
        print(f"{platform}: {status}")
        if status != "valid":
            has_expired = True
            print(f"  需要手动打开 9222 Chrome 重新登录：{platform}")
            print(f"  {results[platform]['message']}")
    print(f"health_status: {HEALTH_STATUS_PATH}")
    return 1 if has_expired else 0


if __name__ == "__main__":
    raise SystemExit(main())
