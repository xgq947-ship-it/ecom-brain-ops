"""Workflow runtime data models.

纯数据结构，不含平台逻辑、不含 IO。step handler 由业务层（workflows/）提供。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from core.runtime.secrets import redact_inputs


@dataclass
class Artifact:
    """workflow 运行过程中产生或引用的产物。"""

    type: str
    role: str = ""
    name: str = ""
    path: str = ""
    platform: str = ""
    month: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "role": self.role,
            "name": self.name,
            "path": self.path,
            "platform": self.platform,
            "month": self.month,
            "metadata": dict(self.metadata),
        }


@dataclass
class WorkflowStep:
    """workflow 中的一个步骤声明。

    handler 接收一个 step context（由 runner 注入），返回 OpsResult。
    required=False 的 step 失败后流程继续；required=True 的 step 失败则中断。
    retryable 本阶段只透传记录，runtime 不做自动重试。
    """

    id: str
    name: str
    handler: Callable[[Any], Any]
    retryable: bool = False
    required: bool = True


@dataclass
class Workflow:
    """一个 step 化的业务流程声明。"""

    id: str
    name: str
    steps: list[WorkflowStep] = field(default_factory=list)


@dataclass
class StepRun:
    """单个 step 的运行记录。"""

    step_id: str
    name: str
    status: str = "pending"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    retryable: bool = False
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "status": self.status,
            "inputs": _jsonable(redact_inputs(self.inputs)),
            "outputs": _jsonable(self.outputs),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "errors": list(self.errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retryable": self.retryable,
            "required": self.required,
        }


@dataclass
class TaskRun:
    """一次 workflow 运行的整体记录。"""

    run_id: str
    workflow_id: str
    status: str = "running"
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    steps: list[StepRun] = field(default_factory=list)
    workflow_name: str = ""
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "status": self.status,
            "dry_run": self.dry_run,
            "inputs": _jsonable(redact_inputs(self.inputs)),
            "outputs": _jsonable(self.outputs),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "errors": list(self.errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "steps": [step.to_dict() for step in self.steps],
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
