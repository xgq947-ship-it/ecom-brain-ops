#!/usr/bin/env python3
"""中文入口兼容 wrapper：猫超店铺商品销售分析。

只把参数透传给 workflow（run.py workflow jst_tmcs_shop_product_sales_analysis ...），
不在本层承载业务主逻辑，也不直接请求平台。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WORKFLOW_ID = "jst_tmcs_shop_product_sales_analysis"


def _run_workflow(workflow_args: list[str]) -> int:
    from run import run_workflow

    return run_workflow(workflow_args)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _run_workflow([WORKFLOW_ID, *args])


if __name__ == "__main__":
    raise SystemExit(main())
