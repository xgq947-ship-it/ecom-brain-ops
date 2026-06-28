"""step handler 的统一返回结构。

handler 不直接操作 StepRun/TaskRun，只返回 OpsResult，由 runner 负责落盘和状态推进。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.runtime.models import Artifact


@dataclass
class OpsResult:
    success: bool
    outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def success_result(
    outputs: dict[str, Any] | None = None,
    artifacts: list[Artifact] | None = None,
) -> OpsResult:
    return OpsResult(
        success=True,
        outputs=dict(outputs or {}),
        artifacts=list(artifacts or []),
    )


def failure_result(
    errors: list[str] | str,
    outputs: dict[str, Any] | None = None,
    artifacts: list[Artifact] | None = None,
) -> OpsResult:
    if isinstance(errors, str):
        errors = [errors]
    return OpsResult(
        success=False,
        outputs=dict(outputs or {}),
        artifacts=list(artifacts or []),
        errors=list(errors),
    )
