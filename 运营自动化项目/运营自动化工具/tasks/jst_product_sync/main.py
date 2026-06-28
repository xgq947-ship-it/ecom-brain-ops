#!/usr/bin/env python3
"""Compatibility wrapper for the JST product sync workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clients.ops_cli_client import run_ops_json  # noqa: E402
from core.config_loader import get_path  # noqa: E402


DEFAULT_KEEP_BRANDS = ("奥克斯", "苏泊尔")
DEFAULT_SOURCE = get_path("jst_product_import_file")
DEFAULT_ROOT = get_path("ecommerce_brain_dir")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新电商Brain内所有聚水潭商品资料")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="保留参数兼容；实际由 Ops-Cli 读取其配置")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="保留参数兼容；实际由 Ops-Cli 递归同步")
    parser.add_argument("--keep-brands", nargs="+", default=list(DEFAULT_KEEP_BRANDS), help="要保留的品牌")
    parser.add_argument("--no-filter", action="store_true", help="保留参数兼容；当前会透传空品牌列表给 Ops-Cli")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不覆盖")
    parser.add_argument("--use-local-only", action="store_true", help="只使用本地现成源文件，不自动从聚水潭后台导出下载")
    return parser.parse_args()


def _run_workflow(workflow_args: list[str]) -> int:
    from run import run_workflow

    return run_workflow(workflow_args)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _run_workflow(["jst_product_sync", *args])


if __name__ == "__main__":
    raise SystemExit(main())
