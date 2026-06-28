#!/usr/bin/env python3
"""猫超营销端风险预警数值读取 — 旧中文入口的薄 wrapper。

`run.py 猫超营销风险预警 ...` -> tasks/tmcs_marketing_risk_warning.py
-> run.py workflow tmcs_marketing_risk_warning ...
真实业务在 workflows/tmcs_marketing_risk_warning/，本文件只做参数透传。
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
    return _run_workflow(["tmcs_marketing_risk_warning", *args])


if __name__ == "__main__":
    raise SystemExit(main())
