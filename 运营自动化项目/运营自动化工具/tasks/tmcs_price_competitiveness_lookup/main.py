#!/usr/bin/env python3
"""猫超价格竞争力查询 — 旧中文入口的薄 wrapper。

`run.py 猫超价格竞争力查询 ...` -> tasks/tmcs_price_competitiveness_lookup/main.py
-> run.py workflow tmcs_price_competitiveness_lookup ...
真实业务在 workflows/tmcs_price_competitiveness_lookup/，本文件只做参数透传。
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
    return _run_workflow(["tmcs_price_competitiveness_lookup", *args])


if __name__ == "__main__":
    raise SystemExit(main())
