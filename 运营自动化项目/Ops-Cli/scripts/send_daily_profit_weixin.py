#!/usr/bin/env python3
from __future__ import annotations

import json
DEPRECATED_MESSAGE = (
    "send_daily_profit_weixin.py 已废弃；日利润通知统一使用 "
    "scripts/send_daily_profit_feishu.py。"
)


def main() -> int:
    print(json.dumps({"success": False, "deprecated": True, "error": DEPRECATED_MESSAGE}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
