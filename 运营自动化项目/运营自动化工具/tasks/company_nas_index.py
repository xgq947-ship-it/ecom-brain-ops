#!/usr/bin/env python3
"""公司网盘产品资料索引 — 旧中文入口的薄 wrapper。

`run.py 更新公司网盘索引 ...` -> tasks/company_nas_index.py -> run.py workflow company_nas_index ...
真实业务在 workflows/company_nas_index/（索引逻辑见 indexer.py，NAS 助手见
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
    return _run_workflow(["company_nas_index", *args])


if __name__ == "__main__":
    raise SystemExit(main())
