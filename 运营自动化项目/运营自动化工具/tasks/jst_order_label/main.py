#!/usr/bin/env python3
"""Compatibility wrapper for the JST order labeling workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clients.ops_cli_client import run_ops_json  # noqa: E402
from core.config_loader import get_path  # noqa: E402


INPUT_PATH = get_path("runtime_dir") / "latest_brush_orders.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="聚水潭刷单订单打标")
    parser.add_argument("--order-id", action="append", default=[], help="订单号，可重复传入")
    parser.add_argument("--input", default=str(INPUT_PATH), help="订单号 JSON 输入文件")
    parser.add_argument("--dry-run", action="store_true", help="只查询订单，不执行备注和标签")
    parser.add_argument("--limit", type=int, help="只处理前 N 单")
    return parser.parse_args()


def _run_workflow(workflow_args: list[str]) -> int:
    from run import run_workflow

    return run_workflow(workflow_args)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _run_workflow(["jst_order_label", *args])


if __name__ == "__main__":
    raise SystemExit(main())
