from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.runtime import (
    Artifact,
    StepContext,
    WorkflowRunner,
    build_workflow,
    failure_result,
    step,
    success_result,
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ok_step(step_id: str, outputs: dict | None = None):
    def handler(ctx: StepContext):
        return success_result(outputs=outputs or {f"{step_id}_ran": True})

    return step(step_id, step_id, handler)


def test_run_produces_run_json_and_step_json(tmp_path: Path) -> None:
    wf = build_workflow(
        "demo",
        "Demo",
        [_ok_step("a"), _ok_step("b")],
    )
    runner = WorkflowRunner(tmp_path)
    run = runner.run(wf, inputs={"foo": "bar"})

    assert run.status == "success"
    assert run.run_id.startswith("run_")
    assert len(run.steps) == 2

    run_dir = runner.last_run_dir
    assert run_dir is not None
    run_json = _read(run_dir / "run.json")
    assert run_json["status"] == "success"
    assert run_json["workflow_id"] == "demo"
    assert run_json["inputs"] == {"foo": "bar"}
    assert len(run_json["steps"]) == 2

    assert _read(run_dir / "steps" / "a.json")["status"] == "success"
    assert _read(run_dir / "steps" / "b.json")["status"] == "success"
    assert (run_dir / "artifacts.json").exists()


def test_run_dir_uses_year_month(tmp_path: Path) -> None:
    wf = build_workflow("demo", "Demo", [_ok_step("a")])
    runner = WorkflowRunner(tmp_path)
    runner.run(wf)
    run_dir = runner.last_run_dir
    # runs_root / YYYY-MM / run_xxx
    assert run_dir.parent.parent == tmp_path
    assert len(run_dir.parent.name) == 7 and run_dir.parent.name[4] == "-"


def test_dry_run_records_status(tmp_path: Path) -> None:
    seen: dict[str, bool] = {}

    def handler(ctx: StepContext):
        seen["dry_run"] = ctx.dry_run
        return success_result()

    wf = build_workflow("demo", "Demo", [step("only", "only", handler)])
    runner = WorkflowRunner(tmp_path)
    run = runner.run(wf, dry_run=True)

    assert seen["dry_run"] is True
    assert run.status == "dry_run_success"
    assert run.steps[0].status == "dry_run_success"
    assert _read(runner.last_run_dir / "steps" / "only.json")["status"] == "dry_run_success"


def test_required_failure_aborts(tmp_path: Path) -> None:
    ran: list[str] = []

    def fail_handler(ctx: StepContext):
        ran.append("bad")
        return failure_result("boom")

    def after_handler(ctx: StepContext):
        ran.append("after")
        return success_result()

    wf = build_workflow(
        "demo",
        "Demo",
        [
            _ok_step("first"),
            step("bad", "bad", fail_handler, required=True),
            step("after", "after", after_handler),
        ],
    )
    runner = WorkflowRunner(tmp_path)
    run = runner.run(wf)

    assert run.status == "failed"
    assert ran == ["bad"]  # after 未执行
    assert len(run.steps) == 2
    assert run.steps[-1].status == "failed"
    assert "boom" in run.errors
    assert not (runner.last_run_dir / "steps" / "after.json").exists()


def test_optional_failure_continues(tmp_path: Path) -> None:
    ran: list[str] = []

    def soft_fail(ctx: StepContext):
        ran.append("soft")
        return failure_result("soft boom")

    def after_handler(ctx: StepContext):
        ran.append("after")
        return success_result()

    wf = build_workflow(
        "demo",
        "Demo",
        [
            step("soft", "soft", soft_fail, required=False),
            step("after", "after", after_handler),
        ],
    )
    runner = WorkflowRunner(tmp_path)
    run = runner.run(wf)

    assert ran == ["soft", "after"]
    assert run.steps[0].status == "failed"
    assert run.steps[1].status == "success"
    # 没有 required step 失败 -> 整体仍算成功
    assert run.status == "success"
    assert _read(runner.last_run_dir / "steps" / "soft.json")["status"] == "failed"
    assert _read(runner.last_run_dir / "steps" / "after.json")["status"] == "success"


def test_artifacts_collected(tmp_path: Path) -> None:
    def handler(ctx: StepContext):
        art = Artifact(type="xlsx", role="output", name="report.xlsx", path="/tmp/report.xlsx", month="2026-05")
        return success_result(artifacts=[art])

    wf = build_workflow("demo", "Demo", [step("make", "make", handler)])
    runner = WorkflowRunner(tmp_path)
    run = runner.run(wf)

    assert len(run.artifacts) == 1
    artifacts_json = _read(runner.last_run_dir / "artifacts.json")
    assert artifacts_json[0]["name"] == "report.xlsx"
    assert artifacts_json[0]["month"] == "2026-05"


def test_handler_exception_becomes_failure(tmp_path: Path) -> None:
    def boom(ctx: StepContext):
        raise RuntimeError("kaboom")

    wf = build_workflow("demo", "Demo", [step("boom", "boom", boom, required=True)])
    runner = WorkflowRunner(tmp_path)
    run = runner.run(wf)

    assert run.status == "failed"
    assert run.steps[0].status == "failed"
    assert any("kaboom" in err for err in run.steps[0].errors)


def test_typed_state_reuses_object_across_steps(tmp_path: Path) -> None:
    @dataclass
    class DemoState:
        count: int = 0

    def first(ctx: StepContext):
        state = ctx.typed_state(DemoState)
        state.count += 1
        return success_result()

    def second(ctx: StepContext):
        state = ctx.typed_state(DemoState)
        return success_result(outputs={"count": state.count})

    wf = build_workflow("typed", "Typed", [step("first", "first", first), step("second", "second", second)])
    run = WorkflowRunner(tmp_path).run(wf)

    assert run.status == "success"
    assert run.outputs["count"] == 1
