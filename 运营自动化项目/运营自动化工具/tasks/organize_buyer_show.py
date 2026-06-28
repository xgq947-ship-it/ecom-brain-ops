#!/usr/bin/env python3
"""买家秀文件整理 - 旧中文入口薄 wrapper，转发到 organize_buyer_show workflow。"""

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
    return _run_workflow(["organize_buyer_show", *args])


if __name__ == "__main__":
    raise SystemExit(main())
