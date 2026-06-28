#!/usr/bin/env python3
"""刷新 AI Knowledge Base 版本文件并执行校验。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / "运营自动化工具"
if str(WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_ROOT))

from workflows.ai_knowledge_base_update.steps import (  # noqa: E402
    Flags,
    run_dispatch_generator,
    run_validation,
    write_version_doc,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="刷新 AI Knowledge Base 版本文件并执行校验")
    parser.add_argument("--source-root", default=str(ROOT))
    parser.add_argument("--kb-root", default=str(Path.home() / "Desktop" / "电商Brain-AI-Knowledge"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    flags = Flags(
        source_root=Path(args.source_root).expanduser(),
        kb_root=Path(args.kb_root).expanduser(),
        prompt_file=WORKFLOW_ROOT / "workflows" / "ai_knowledge_base_update" / "prompt_template.md",
        updates_file=None,
        max_docs=None,
        dry_run=args.dry_run,
    )

    pending = write_version_doc(
        flags=flags,
        apply_result={"applied_files": [], "changed_count": 0, "needs_ai_apply": True},
        validation={"skipped": True, "reason": "finalize pending validate"},
        dry_run=flags.dry_run,
    )

    if flags.dry_run:
        print(json.dumps({"success": True, "dry_run": True, "version_file": pending}, ensure_ascii=False, indent=2))
        return 0

    # 校验前先确定性重建派发表，满足校验器的 dispatch/fresh 闸门（ADR-003）
    dispatch = run_dispatch_generator(flags.source_root, flags.kb_root)
    if not dispatch["success"]:
        print(json.dumps({"success": False, "stage": "dispatch", "dispatch": dispatch}, ensure_ascii=False, indent=2))
        return 1

    validation = run_validation(flags.source_root / "scripts" / "validate_ai_knowledge_base.py", flags.source_root)
    final_result = write_version_doc(
        flags=flags,
        apply_result={"applied_files": [], "changed_count": 0, "needs_ai_apply": True},
        validation=validation,
        dry_run=False,
    )
    payload = {"success": validation["success"], "version_file": final_result, "dispatch": dispatch, "validation": validation}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if validation["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
