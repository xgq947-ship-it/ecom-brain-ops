#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from scene.api import SessionHubError, get_session
from scene.chrome_cdp import check_cdp, chrome_start_command, start_chrome
from scene.session_check import check_session
from scene.session_store import SessionStore
from scene.site_config import ConfigError, load_site_config, target_url_for
from scene.token_capture import CaptureError, capture_session


ROOT = Path(__file__).resolve().parent


def setup_logging() -> None:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"sessionhub_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_chrome(args: argparse.Namespace) -> int:
    if args.chrome_command == "start":
        ok, msg = start_chrome()
        print(msg)
        if ok:
            print("专用 Chrome Profile：$HOME/.sessionhub/chrome-9222")
            return 0
        print("你也可以手动运行：")
        print(chrome_start_command())
        return 1
    if args.chrome_command == "check":
        ok, msg = check_cdp()
        print(msg)
        if not ok:
            print("启动命令：")
            print(chrome_start_command())
        return 0 if ok else 1
    return 1


def cmd_init(args: argparse.Namespace) -> int:
    try:
        config = load_site_config(args.site)
        url = target_url_for(config)
    except ConfigError as exc:
        print(exc)
        return 1
    print(f"将打开站点：{config.get('name', args.site)}")
    print(f"目标页面：{url}")
    print("脚本会自动启动专用 Chrome。")
    print("如果页面要求登录，请在弹出的 Chrome 里完成登录并进入目标页面。")
    try:
        data = capture_session(args.site, "download_file_query", wait_seconds=args.wait)
    except CaptureError as exc:
        print(exc)
        return 1
    print(f"已捕获并保存：{data['saved_to']}")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    try:
        data = capture_session(args.site, args.scene, wait_seconds=args.wait)
    except (CaptureError, ConfigError) as exc:
        print(exc)
        return 1
    print(f"已捕获并保存：{data['saved_to']}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    try:
        data = check_session(args.site, args.scene)
    except Exception as exc:
        print(exc)
        return 1
    result = data.get("check_result") or {}
    status = "可用" if data.get("status") == "valid" else "不可用"
    print(f"{args.site}/{args.scene}：{status}")
    print(f"原因：{result.get('reason', '')}")
    print(f"HTTP：{result.get('status_code')}")
    print(f"last_check：{result.get('last_check')}")
    return 0 if data.get("status") == "valid" else 1


def cmd_get(args: argparse.Namespace) -> int:
    try:
        data = get_session(args.site, args.scene)
    except SessionHubError as exc:
        print(exc)
        print("处理建议：脚本已尽量自动启动专用 Chrome；请在弹出的 Chrome 里登录后台并刷新目标页面后重试。")
        return 1
    print_json(data)
    return 0


def _print_rows_plain(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("暂无已保存 session。")
        return
    print(f"{'site':<18} {'scene':<24} {'status':<10} {'updated_at':<25} last_check")
    for row in rows:
        last_check = row.get("last_check") or ""
        if isinstance(last_check, dict):
            last_check = last_check.get("last_check", "")
        print(
            f"{row.get('site',''):<18} {row.get('scene',''):<24} "
            f"{row.get('status',''):<10} {row.get('updated_at',''):<25} {last_check}"
        )


def cmd_list(args: argparse.Namespace) -> int:
    rows = SessionStore().list_sessions()
    try:
        from rich.table import Table
        from rich.console import Console

        table = Table(title="SessionHub Sessions")
        for col in ("site", "scene", "status", "updated_at", "last_check"):
            table.add_column(col)
        for row in rows:
            last_check = row.get("last_check") or ""
            if isinstance(last_check, dict):
                last_check = last_check.get("last_check", "")
            table.add_row(
                str(row.get("site", "")),
                str(row.get("scene", "")),
                str(row.get("status", "")),
                str(row.get("updated_at", "")),
                str(last_check),
            )
        if rows:
            Console().print(table)
        else:
            print("暂无已保存 session。")
    except ModuleNotFoundError:
        _print_rows_plain(rows)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sessionhub.py",
        description="SessionHub：复用专用 Chrome 登录态，捕获并提供动态 session。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    chrome = sub.add_parser("chrome", help="管理专用 Chrome CDP")
    chrome_sub = chrome.add_subparsers(dest="chrome_command", required=True)
    chrome_sub.add_parser("start", help="启动专用 Chrome")
    chrome_sub.add_parser("check", help="检查 Chrome CDP 是否可用")
    chrome.set_defaults(func=cmd_chrome)

    init = sub.add_parser("init", help="初始化站点登录态并捕获默认场景")
    init.add_argument("site")
    init.add_argument("--wait", type=int, default=90, help="监听秒数，默认 90")
    init.set_defaults(func=cmd_init)

    capture = sub.add_parser("capture", help="捕获指定场景 session")
    capture.add_argument("site")
    capture.add_argument("--scene", default="download_file_query")
    capture.add_argument("--wait", type=int, default=90, help="监听秒数，默认 90")
    capture.set_defaults(func=cmd_capture)

    check = sub.add_parser("check", help="检查 session 是否可用")
    check.add_argument("site")
    check.add_argument("--scene", default="download_file_query")
    check.set_defaults(func=cmd_check)

    get = sub.add_parser("get", help="输出可供其他 Skill 调用的 session JSON")
    get.add_argument("site")
    get.add_argument("--scene", default="download_file_query")
    get.set_defaults(func=cmd_get)

    list_cmd = sub.add_parser("list", help="列出所有站点和状态")
    list_cmd.set_defaults(func=cmd_list)
    return parser


def main() -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("已取消。")
        return 130
    except Exception as exc:
        logging.exception("未处理异常")
        print(f"执行失败：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
