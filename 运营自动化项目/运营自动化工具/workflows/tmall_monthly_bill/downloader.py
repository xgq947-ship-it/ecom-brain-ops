#!/usr/bin/env python3
"""Delegate TMCS bill download to Ops-Cli and keep the legacy entry stable."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clients.ops_cli_client import run_ops_json  # noqa: E402
from core.config_loader import get_path  # noqa: E402


DEFAULT_OUTPUT_DIR = get_path("tmall_bill_download_dir")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过 Ops-Cli 下载猫超账单")
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--last-month", action="store_true", help="下载上月账单")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="保留参数兼容；实际由 Ops-Cli 控制下载目录")
    parser.add_argument("--download-statement-list", action="store_true", help="同时导出对账单列表")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不下载")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = ["--json", "tmcs", "bill", "download"]
    if args.last_month:
        command.append("--last-month")
    if args.start:
        command.extend(["--start", args.start])
    if args.end:
        command.extend(["--end", args.end])
    if args.download_statement_list:
        command.append("--download-statement-list")
    if args.dry_run:
        command.append("--dry-run")

    payload = run_ops_json(command, interactive_recovery=not args.dry_run)
    result = {
        "success": bool(payload.get("success")),
        "task": "tmcs_bill_download",
        "platform": payload.get("platform"),
        "command": payload.get("command"),
        "ops_result": payload,
        "compat_ignored": {
            "output_dir": args.output_dir,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
