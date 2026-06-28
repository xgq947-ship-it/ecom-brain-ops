#!/usr/bin/env python3
"""创建智多星全站推广计划 — 旧中文入口的薄 wrapper。

`run.py 创建智多星全站推广计划 ...` -> tasks/tmcs_zdx_fullsite_plan_create.py -> run.py workflow tmcs_zdx_fullsite_plan_create ...
真实业务在 workflows/tmcs_zdx_fullsite_plan_create/，本文件只做参数透传。
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
    return _run_workflow(["tmcs_zdx_fullsite_plan_create", *args])


if __name__ == "__main__":
    raise SystemExit(main())
