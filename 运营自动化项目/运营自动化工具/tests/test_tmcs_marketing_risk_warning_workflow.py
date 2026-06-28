from __future__ import annotations

from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from core.task_registry import resolve_task

from workflows.tmcs_marketing_risk_warning import steps
from workflows.tmcs_marketing_risk_warning.workflow import build_workflow


def _payload(count: int, *, dry_run: bool = False, simulated: bool = False) -> dict:
    return {
        "success": True,
        "platform": "tmcs",
        "command": "marketing risk-warning count",
        "data": {
            "risk_warning_count": count,
            "label_text": f"风险预警（{count}）",
            "source": "simulated" if simulated else "page",
            "simulated": simulated,
            "scene": "tmall_chaoshi/marketing_risk_warning_count",
            "dry_run": dry_run,
            "artifacts": [],
            "context_path": "/tmp/x.json",
        },
    }


def _run(monkeypatch, tmp_path: Path, args: list[str], *, dry_run: bool, payload: dict | Exception):
    seen: list = []

    def fake_run_ops_json(command, interactive_recovery=None):
        seen.append((list(command), interactive_recovery))
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)
    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": dry_run, "args": args}, dry_run=dry_run)
    return run, seen, runner


def _step_outputs(runner: WorkflowRunner, step_id: str) -> dict:
    import json

    path = runner.last_run_dir / "steps" / f"{step_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["outputs"]


def test_workflow_registers() -> None:
    wf = discover_workflow("tmcs_marketing_risk_warning")
    assert wf.id == "tmcs_marketing_risk_warning"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "fetch_risk_warning_count",
        "collect_outputs",
    ]


def test_chinese_alias_resolves() -> None:
    assert resolve_task("猫超营销风险预警") == "tmcs_marketing_risk_warning"
    assert resolve_task("天猫超市营销风险预警") == "tmcs_marketing_risk_warning"
    assert resolve_task("营销端风险预警") == "tmcs_marketing_risk_warning"
    assert resolve_task("猫超风险预警") == "tmcs_marketing_risk_warning"
    assert resolve_task("风险预警数值") == "tmcs_marketing_risk_warning"


def test_count_three_from_page(monkeypatch, tmp_path: Path) -> None:
    run, _, runner = _run(
        monkeypatch,
        tmp_path,
        args=[],
        dry_run=False,
        payload=_payload(count=3),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["risk_warning_count"] == 3
    assert out["label_text"] == "风险预警（3）"
    assert out["source"] == "page"
    assert out["simulated"] is False


def test_count_zero_from_page(monkeypatch, tmp_path: Path) -> None:
    run, _, runner = _run(
        monkeypatch,
        tmp_path,
        args=[],
        dry_run=False,
        payload=_payload(count=0),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["risk_warning_count"] == 0


def test_dry_run_forwards_flag_and_skips_real_call(monkeypatch, tmp_path: Path) -> None:
    run, seen, runner = _run(
        monkeypatch,
        tmp_path,
        args=["--dry-run"],
        dry_run=True,
        payload=_payload(count=0, dry_run=True, simulated=True),
    )
    assert run.status == "dry_run_success"
    command, interactive = seen[0]
    assert "--dry-run" in command
    assert interactive is False
    out = _step_outputs(runner, "collect_outputs")
    assert out["dry_run"] is True
    assert out["simulated"] is True


def test_dry_run_does_not_invoke_subprocess(monkeypatch, tmp_path: Path) -> None:
    """dry-run 路径必须走 monkeypatched run_ops_json，绝不真实 subprocess.run。"""
    import subprocess

    def boom(*args, **kwargs):
        raise AssertionError("dry-run 不应进入真实 subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    run, _, _ = _run(
        monkeypatch,
        tmp_path,
        args=["--dry-run"],
        dry_run=True,
        payload=_payload(count=0, dry_run=True, simulated=True),
    )
    assert run.status == "dry_run_success"


def test_ops_failure_propagates(monkeypatch, tmp_path: Path) -> None:
    run, _, _ = _run(
        monkeypatch,
        tmp_path,
        args=[],
        dry_run=False,
        payload=RuntimeError("Ops-Cli 执行失败 [RISK_WARNING_COUNT_NOT_FOUND]：未找到"),
    )
    assert run.status == "failed"
    assert any("RISK_WARNING_COUNT_NOT_FOUND" in err for err in run.errors)
