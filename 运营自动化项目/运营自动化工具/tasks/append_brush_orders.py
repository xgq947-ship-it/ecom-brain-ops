#!/usr/bin/env python3
"""刷单表格登记 — 旧中文入口的薄 wrapper。

`run.py 刷单表格登记 ...` -> tasks/append_brush_orders.py -> run.py workflow append_brush_orders ...
真实业务在 workflows/append_brush_orders/（业务逻辑见 appender.py），本文件只做参数透传。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_workflow(workflow_args: list[str]) -> int:
    from run import run_workflow

    return run_workflow(workflow_args)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _run_workflow(["append_brush_orders", *args])


if __name__ == "__main__":
    raise SystemExit(main())
