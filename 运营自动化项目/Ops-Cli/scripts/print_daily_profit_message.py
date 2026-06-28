#!/usr/bin/env python3
from __future__ import annotations

from send_daily_profit_feishu import format_message, run_profit_query


def main() -> int:
    print(format_message(run_profit_query()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
