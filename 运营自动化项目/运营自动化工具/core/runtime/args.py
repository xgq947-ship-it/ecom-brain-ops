from __future__ import annotations

import argparse


class WorkflowArgumentError(ValueError):
    """Raised when workflow CLI arguments are invalid."""


def parse_workflow_args(parser: argparse.ArgumentParser, args: list[str]) -> argparse.Namespace:
    """Parse workflow args and fail on unknown options instead of silently ignoring them."""
    try:
        namespace, unknown = parser.parse_known_args(args)
    except SystemExit as exc:
        raise WorkflowArgumentError(f"参数解析失败：exit={exc.code}") from exc
    if unknown:
        raise WorkflowArgumentError(f"未知参数：{' '.join(unknown)}")
    return namespace
