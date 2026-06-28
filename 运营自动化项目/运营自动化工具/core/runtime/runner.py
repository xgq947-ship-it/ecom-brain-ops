"""WorkflowRunner — 顺序执行 workflow step，落盘 run.json / steps / artifacts。

执行语义：
- dry_run 仍调用 handler；handler 自行负责 dry-run 时不触发真实副作用。
- step 成功 -> StepRun.status = "success"（dry_run 下为 "dry_run_success"）。
- step 失败：
    - required=True  -> 中断，TaskRun.status = "failed"。
    - required=False -> 记 failed 但继续，TaskRun 最终状态视其余 required step 而定。
- handler 抛异常等价于 failure。
- retryable 只透传记录，runtime 不做自动重试。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from core.runtime.models import Artifact, StepRun, TaskRun, Workflow
from core.runtime.result import OpsResult, failure_result
from core.runtime.storage import RunStorage

T = TypeVar("T")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_run_id() -> str:
    return "run_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")


class StepContext:
    """注入给每个 step handler 的轻量上下文。

    handler 通过 ctx.dry_run 判断是否真实执行，通过 ctx.outputs 读取此前 step 的累积输出，
    通过 ctx.add_artifact 收集产物（也可直接在 OpsResult 里返回 artifacts）。
    """

    def __init__(self, *, run: TaskRun, inputs: dict[str, Any], dry_run: bool) -> None:
        self.run = run
        self.inputs = dict(inputs)
        self.dry_run = dry_run
        self.outputs: dict[str, Any] = {}
        # state：跨 step 传递 Python 对象的暂存区，不参与 JSON 落盘。
        self.state: dict[str, Any] = {}
        self._step_artifacts: list[Artifact] = []

    def add_artifact(self, artifact: Artifact) -> None:
        self._step_artifacts.append(artifact)

    def collected_artifacts(self) -> list[Artifact]:
        return list(self._step_artifacts)

    def typed_state(self, factory: Callable[[], T], *, key: str | None = None) -> T:
        """Return a workflow-local typed state object, creating it once per run."""
        state_key = key or f"typed:{getattr(factory, '__module__', '')}:{getattr(factory, '__qualname__', repr(factory))}"
        if state_key not in self.state:
            self.state[state_key] = factory()
        return self.state[state_key]


class WorkflowRunner:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = Path(runs_root)
        self.last_run_dir: Path | None = None

    def run(
        self,
        workflow: Workflow,
        *,
        inputs: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> TaskRun:
        inputs = dict(inputs or {})
        run = TaskRun(
            run_id=_new_run_id(),
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            status="running",
            inputs=inputs,
            dry_run=dry_run,
            started_at=_now(),
        )
        storage = RunStorage(run, self.runs_root)
        storage.write_run()  # 初始落盘，崩溃时也有痕迹

        ctx = StepContext(run=run, inputs=inputs, dry_run=dry_run)
        aborted = False
        any_required_failure = False

        for step in workflow.steps:
            step_run = StepRun(
                step_id=step.id,
                name=step.name,
                status="running",
                inputs=dict(inputs),
                started_at=_now(),
                retryable=step.retryable,
                required=step.required,
            )
            run.steps.append(step_run)

            result = self._invoke(step.handler, ctx)

            step_run.outputs = dict(result.outputs)
            step_run.errors = list(result.errors)
            step_artifacts = list(result.artifacts) + ctx.collected_artifacts()
            step_run.artifacts = step_artifacts
            run.artifacts.extend(step_artifacts)
            ctx.outputs.update(result.outputs)
            run.outputs.update(result.outputs)
            ctx._step_artifacts.clear()
            step_run.finished_at = _now()

            if result.success:
                step_run.status = "dry_run_success" if dry_run else "success"
            else:
                step_run.status = "failed"
                run.errors.extend(result.errors)
                if step.required:
                    any_required_failure = True
                    storage.write_step(step_run)
                    aborted = True
                    break

            storage.write_step(step_run)

        run.finished_at = _now()
        if aborted or any_required_failure:
            run.status = "failed"
        elif dry_run:
            run.status = "dry_run_success"
        else:
            run.status = "success"

        storage.write_artifacts()
        storage.write_run()
        self.last_run_dir = storage.run_dir
        try:
            from core.runtime.archive import RunIndex

            RunIndex(self.runs_root).append(run, storage.run_dir)
        except Exception:  # noqa: BLE001 - 归档索引失败不应影响主流程
            pass
        return run

    @staticmethod
    def _invoke(handler, ctx: StepContext) -> OpsResult:
        try:
            result = handler(ctx)
        except Exception as exc:  # noqa: BLE001 - handler 异常统一转 failure
            return failure_result([f"{type(exc).__name__}: {exc}"])
        if not isinstance(result, OpsResult):
            return failure_result([f"step handler 未返回 OpsResult：{type(result).__name__}"])
        return result
