#!/usr/bin/env python3
"""聚水潭短信验证码提交 — 中文入口薄 wrapper。

`run.py 聚水潭短信验证码提交 ...` -> tasks/jst_sms_verification_submit.py
-> run.py workflow jst_sms_verification_submit ...
真实业务在 workflows/jst_sms_verification_submit/，本文件只做参数透传。
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
    return _run_workflow(["jst_sms_verification_submit", *args])


if __name__ == "__main__":
    raise SystemExit(main())
