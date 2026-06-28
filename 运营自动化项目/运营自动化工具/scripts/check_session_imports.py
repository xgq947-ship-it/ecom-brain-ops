#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (ROOT / "tasks", ROOT / "clients")
ALLOWED = {
    ROOT / "clients" / "ops_cli_client.py",
}
BLOCKED_SNIPPETS = (
    "from src.api",
    "import src.api",
    "from src.site_config",
    "import src.site_config",
    "sessionhub/src",
    "sys.path.insert(0, str(SESSIONHUB_ROOT))",
    "sys.path.insert(0, str(sessionhub_dir))",
)


def main() -> int:
    violations: list[str] = []
    for base in SCAN_DIRS:
        for path in sorted(base.rglob("*.py")):
            if path in ALLOWED:
                continue
            text = path.read_text(encoding="utf-8")
            for snippet in BLOCKED_SNIPPETS:
                if snippet in text:
                    violations.append(f"{path.relative_to(ROOT)}: blocked session import: {snippet}")
    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print("session import check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
