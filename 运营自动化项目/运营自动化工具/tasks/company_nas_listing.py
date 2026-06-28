#!/usr/bin/env python3
"""公司网盘下载产品 — 旧中文入口的薄 wrapper。

`run.py 公司网盘下载产品 ...` -> tasks/company_nas_listing.py -> run.py workflow company_nas_listing ...
真实业务在 workflows/company_nas_listing/（业务逻辑见 listing.py，NAS 助手见
workflows/company_nas_common/nas.py），本文件只做参数透传。
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
    return _run_workflow(["company_nas_listing", *args])


if __name__ == "__main__":
    raise SystemExit(main())
