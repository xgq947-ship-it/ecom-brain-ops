"""Workflow 构造与校验辅助，不含具体业务流程。"""

from __future__ import annotations

from core.runtime.models import Workflow, WorkflowStep


def build_workflow(workflow_id: str, name: str, steps: list[WorkflowStep]) -> Workflow:
    """构造一个 Workflow 并做基础校验（step id 唯一、非空）。"""
    if not workflow_id:
        raise ValueError("workflow id 不能为空")
    if not steps:
        raise ValueError(f"workflow {workflow_id} 至少需要一个 step")
    seen: set[str] = set()
    for step in steps:
        if not step.id:
            raise ValueError(f"workflow {workflow_id} 存在空 step id")
        if step.id in seen:
            raise ValueError(f"workflow {workflow_id} 存在重复 step id：{step.id}")
        seen.add(step.id)
    return Workflow(id=workflow_id, name=name or workflow_id, steps=list(steps))


def step(
    step_id: str,
    name: str,
    handler,
    *,
    required: bool = True,
    retryable: bool = False,
) -> WorkflowStep:
    return WorkflowStep(
        id=step_id,
        name=name or step_id,
        handler=handler,
        required=required,
        retryable=retryable,
    )


def get_step(workflow: Workflow, step_id: str) -> WorkflowStep | None:
    for item in workflow.steps:
        if item.id == step_id:
            return item
    return None
