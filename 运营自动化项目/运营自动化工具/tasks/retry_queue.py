#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.retry_queue import list_retries, mark_done, replay_all, replay_retry  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="查看或重放失败任务队列")
    parser.add_argument("retry_id", nargs="?", help="指定 retry_id；不填则查看队列")
    parser.add_argument("--all", action="store_true", help="重放全部 pending retry")
    parser.add_argument("--done", action="store_true", help="把指定 retry_id 标记为 done")
    parser.add_argument("--execute", action="store_true", help="允许真实执行；默认只 dry-run")
    return parser.parse_args()


def print_retries(rows: list[dict]) -> None:
    if not rows:
        print("重试队列为空。")
        return
    print(f"待重试任务：{len(rows)} 个")
    for row in rows:
        print(f"- {row.get('retry_id')} | {row.get('task_name')} | {row.get('reason')} | {row.get('payload')}")


def _run_workflow(workflow_args: list[str]) -> int:
    from run import run_workflow

    return run_workflow(workflow_args)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _run_workflow(["retry_queue", *args])


if __name__ == "__main__":
    raise SystemExit(main())
