#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import check_login, normalize_platform  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 Chrome、导出 Cookie 并检查登录态")
    parser.add_argument("platform", help="tmall 或 jst")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    platform = normalize_platform(args.platform)
    subprocess.run([str(ROOT / "browser" / "start_chrome_9222.sh")], check=True)
    subprocess.run([sys.executable, str(ROOT / "session" / "export_cookies.py"), platform], check=True)
    result = check_login(platform)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("valid") else 1


if __name__ == "__main__":
    raise SystemExit(main())
