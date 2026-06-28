"""demo workflow：验证 workflow runtime 入口的最小可运行流程。

纯内存、dry-run 安全、不触发任何平台动作，仅用于验收 `run.py workflow demo`。
"""

from __future__ import annotations

from core.runtime import (
    Artifact,
    StepContext,
    Workflow,
    build_workflow as _make_workflow,
    success_result,
    step,
)


def _check_inputs(ctx: StepContext):
    return success_result(outputs={"inputs_seen": dict(ctx.inputs), "dry_run": ctx.dry_run})


def _collect_artifact(ctx: StepContext):
    ctx.add_artifact(
        Artifact(
            type="demo",
            role="example",
            name="demo_artifact",
            path="",
            metadata={"note": "演示产物，不写真实文件"},
        )
    )
    return success_result(outputs={"collected": True})


def build_workflow() -> Workflow:
    return _make_workflow(
        "demo",
        "Demo Workflow",
        [
            step("check_inputs", "检查输入", _check_inputs),
            step("collect_artifact", "收集演示产物", _collect_artifact),
        ],
    )
