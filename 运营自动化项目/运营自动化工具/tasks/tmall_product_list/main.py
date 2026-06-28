#!/usr/bin/env python3
"""Compatibility wrapper for the TMCS product list workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clients.ops_cli_client import run_ops_json  # noqa: E402
from core.config_loader import get_path  # noqa: E402


DEFAULT_IMPORT_FILE = get_path("tmall_goods_import_file")
DEFAULT_SYNC_ROOT = get_path("ecommerce_brain_dir")
LATEST_FILE_NAME = get_path("tmall_goods_master_file")
JST_FILE_NAME = get_path("jst_product_master_file")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新猫超商品列表，并用聚水潭商品编码修正新增条码")
    parser.add_argument("--import-file", default=str(DEFAULT_IMPORT_FILE), help="保留参数兼容；实际由 Ops-Cli 读取其配置")
    parser.add_argument("--latest-file", default=str(LATEST_FILE_NAME), help="保留参数兼容")
    parser.add_argument("--jst-file", default=str(JST_FILE_NAME), help="保留参数兼容")
    parser.add_argument("--sync-root", default=str(DEFAULT_SYNC_ROOT), help="保留参数兼容")
    parser.add_argument("--skip-auto-download", action="store_true", help="不自动从猫超后台拉取商品列表，只读取现有导入表")
    parser.add_argument("--force-refresh", action="store_true", help="即使导入表已存在，也强制通过 Ops-Cli 重新拉取")
    parser.add_argument("--wait-login", dest="wait_login", action="store_true", default=True, help="保留参数兼容")
    parser.add_argument("--no-wait-login", dest="wait_login", action="store_false", help="保留参数兼容")
    parser.add_argument("--auto-capture", dest="auto_capture", action="store_true", default=True, help="保留参数兼容")
    parser.add_argument("--no-auto-capture", dest="auto_capture", action="store_false", help="保留参数兼容")
    parser.add_argument("--page-size", type=int, default=100, help="保留参数兼容")
    parser.add_argument("--sync-all", action=argparse.BooleanOptionalAction, default=True, help="保留参数兼容")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写入主表")
    return parser.parse_args()


def _run_workflow(workflow_args: list[str]) -> int:
    from run import run_workflow

    return run_workflow(workflow_args)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _run_workflow(["tmall_product_list", *args])


if __name__ == "__main__":
    raise SystemExit(main())
