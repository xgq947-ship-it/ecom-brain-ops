#!/usr/bin/env python3
"""猫超月账单整理 — 旧中文入口的薄 wrapper。

`run.py 猫超账单整理 ...` -> tasks/tmall_monthly_bill/main.py -> run.py workflow tmall_monthly_bill ...
真实业务在 workflows/tmall_monthly_bill/（业务逻辑见 billing.py + processor.py + services/），
本文件只做参数透传。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run_workflow(workflow_args: list[str]) -> int:
    from run import run_workflow

    return run_workflow(workflow_args)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _run_workflow(["tmall_monthly_bill", *args])


if __name__ == "__main__":
    raise SystemExit(main())
