from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.jst_order_logistics import steps
from workflows.jst_order_logistics.workflow import build_workflow


def test_workflow_registers() -> None:
    wf = discover_workflow("jst_order_logistics")
    assert wf.id == "jst_order_logistics"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "fetch_logistics",
        "write_output",
        "collect_outputs",
    ]


def test_dry_run_never_queries_platform(monkeypatch, tmp_path: Path) -> None:
    calls: list = []

    def fake(command, interactive_recovery=True):
        calls.append((list(command), interactive_recovery))
        return {"success": True, "platform": "jst", "command": "order logistics", "data": {}}

    monkeypatch.setattr(steps, "run_ops_json", fake)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--order-id", "ORDER001"]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert calls == []  # dry-run 绝不发起真实查询

    fetch_step = json.loads(
        (runner.last_run_dir / "steps" / "fetch_logistics.json").read_text(encoding="utf-8")
    )
    assert fetch_step["outputs"]["skipped"] is True


def test_missing_identifier_fails(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": []},
        dry_run=False,
    )
    assert run.status == "failed"


def test_real_run_passthrough_ops_contract(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    ops_data = {
        "site": "erp321",
        "scene": "order_list",
        "summary": {"total": 2, "success": 2, "failed": 0},
        "items": [{"order_id": "A", "logistics_no": "SF1"}, {"order_id": "B", "logistics_no": "SF2"}],
    }

    def fake(command, interactive_recovery=True):
        calls.append((list(command), interactive_recovery))
        return {"success": True, "platform": "jst", "command": "order logistics", "data": ops_data}

    monkeypatch.setattr(steps, "run_ops_json", fake)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--order-id", "A", "--order-id", "B"]},
        dry_run=False,
    )

    assert run.status == "success"
    assert len(calls) == 1
    command, interactive = calls[0]
    assert interactive is True
    assert command.count("--order-id") == 2
    assert "A" in command and "B" in command

    collect_step = json.loads(
        (runner.last_run_dir / "steps" / "collect_outputs.json").read_text(encoding="utf-8")
    )
    outputs = collect_step["outputs"]
    # 对齐 ops-cli 契约
    assert outputs["success"] is True
    assert outputs["platform"] == "jst"
    assert outputs["command"] == "order logistics"
    assert outputs["data"] == ops_data


def test_output_file_written_with_artifact(monkeypatch, tmp_path: Path) -> None:
    out_file = tmp_path / "logistics_result.json"

    def fake(command, interactive_recovery=True):
        return {
            "success": True,
            "platform": "jst",
            "command": "order logistics",
            "data": {"logistics_no": "SF1", "logistics_status": "已签收", "trace_events": []},
        }

    monkeypatch.setattr(steps, "run_ops_json", fake)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--order-id", "A", "--output", str(out_file)]},
        dry_run=False,
    )

    assert run.status == "success"
    assert out_file.exists()
    document = json.loads(out_file.read_text(encoding="utf-8"))
    assert document["success"] is True
    assert document["data"]["logistics_no"] == "SF1"

    artifact_roles = [a.role for a in run.artifacts]
    assert "logistics_result" in artifact_roles
