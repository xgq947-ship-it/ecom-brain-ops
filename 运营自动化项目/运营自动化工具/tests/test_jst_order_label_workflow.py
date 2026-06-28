from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.jst_order_label import steps
from workflows.jst_order_label.workflow import build_workflow


def test_workflow_registers() -> None:
    wf = discover_workflow("jst_order_label")
    assert wf.id == "jst_order_label"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "load_orders",
        "preview_labels",
        "apply_labels",
        "collect_outputs",
    ]


def test_dry_run_never_executes(monkeypatch, tmp_path: Path) -> None:
    calls: list = []

    def fake(command, interactive_recovery):
        calls.append((list(command), interactive_recovery))
        return {"success": True, "data": {}}

    monkeypatch.setattr(steps, "run_ops_json", fake)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--limit", "1", "--order-id", "ORDER001"]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert len(calls) == 1  # 只有 preview 调用一次
    command, interactive = calls[0]
    assert "--execute" not in command
    assert interactive is False
    assert "--limit" in command and "1" in command

    apply_step = json.loads((runner.last_run_dir / "steps" / "apply_labels.json").read_text(encoding="utf-8"))
    assert apply_step["outputs"]["skipped"] is True


def test_real_run_executes_with_flag(monkeypatch, tmp_path: Path) -> None:
    calls: list = []

    def fake(command, interactive_recovery):
        calls.append((list(command), interactive_recovery))
        return {"success": True, "data": {"failed_file": None}}

    monkeypatch.setattr(steps, "run_ops_json", fake)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--order-id", "ORDER001"]},
        dry_run=False,
    )

    assert run.status == "success"
    assert len(calls) == 1  # 只有 apply 调用一次
    command, interactive = calls[0]
    assert "--execute" in command
    assert interactive is True
