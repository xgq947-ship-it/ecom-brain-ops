from __future__ import annotations

from pathlib import Path

import pytest

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from core.task_registry import resolve_task

from workflows.tmcs_xp_workorder_watch import steps
from workflows.tmcs_xp_workorder_watch.workflow import build_workflow


def _payload(count: int, threshold: int, *, dry_run: bool = False, simulated: bool = False) -> dict:
    return {
        "success": True,
        "platform": "tmcs",
        "command": "xp-workorder count",
        "data": {
            "count": count,
            "threshold": threshold,
            "exceeded": count > threshold,
            "source": "simulated" if simulated else "api",
            "simulated": simulated,
            "scene": "tmall_chaoshi/xp_workorder_count",
            "dry_run": dry_run,
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


def test_workflow_registers() -> None:
    wf = discover_workflow("tmcs_xp_workorder_watch")
    assert wf.id == "tmcs_xp_workorder_watch"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "fetch_workorder_count",
        "evaluate_threshold",
        "collect_outputs",
    ]


def test_default_threshold_is_4(monkeypatch, tmp_path: Path) -> None:
    run, seen, _ = _run(
        monkeypatch,
        tmp_path,
        args=["--dry-run"],
        dry_run=True,
        payload=_payload(count=0, threshold=4, dry_run=True, simulated=True),
    )
    assert run.status == "dry_run_success"
    command, _ = seen[0]
    assert "--threshold" in command
    assert command[command.index("--threshold") + 1] == "4"


def test_count_exceeds_threshold(monkeypatch, tmp_path: Path) -> None:
    run, _, runner = _run(
        monkeypatch,
        tmp_path,
        args=["--threshold", "4"],
        dry_run=False,
        payload=_payload(count=5, threshold=4),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["count"] == 5
    assert out["threshold"] == 4
    assert out["exceeded"] is True
    assert "猫超 XP 工单提醒" in out["message"]
    assert "**1**" in out["message"]
    assert "待处理" in out["message"]
    assert out["notification"]["sent"] is False


def test_count_equals_threshold_not_exceeded(monkeypatch, tmp_path: Path) -> None:
    run, _, runner = _run(
        monkeypatch,
        tmp_path,
        args=["--threshold", "4"],
        dry_run=False,
        payload=_payload(count=4, threshold=4),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["exceeded"] is False
    assert out["message"] == "当前猫超 XP 工单数量：4"
    assert out["notification"]["sent"] is False


def test_count_below_threshold_not_exceeded(monkeypatch, tmp_path: Path) -> None:
    run, _, runner = _run(
        monkeypatch,
        tmp_path,
        args=["--threshold", "4"],
        dry_run=False,
        payload=_payload(count=3, threshold=4),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["count"] == 3
    assert out["exceeded"] is False
    assert out["notification"]["sent"] is False


def test_notify_flag_only_returns_delegate_status(monkeypatch, tmp_path: Path) -> None:
    run, _, runner = _run(
        monkeypatch,
        tmp_path,
        args=["--threshold", "4", "--notify"],
        dry_run=False,
        payload=_payload(count=5, threshold=4),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["notification"] == {
        "sent": False,
        "reason": "通知由外层 watchdog 发送，workflow 仅返回提醒内容",
        "delegate": "watchdog_wrapper",
    }


def test_dry_run_forwards_flag_and_skips_real_call(monkeypatch, tmp_path: Path) -> None:
    run, seen, _ = _run(
        monkeypatch,
        tmp_path,
        args=["--threshold", "4", "--dry-run"],
        dry_run=True,
        payload=_payload(count=0, threshold=4, dry_run=True, simulated=True),
    )
    assert run.status == "dry_run_success"
    command, interactive = seen[0]
    assert "--dry-run" in command
    assert interactive is False


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
        payload=_payload(count=0, threshold=4, dry_run=True, simulated=True),
    )
    assert run.status == "dry_run_success"


def test_ops_failure_propagates(monkeypatch, tmp_path: Path) -> None:
    run, _, _ = _run(
        monkeypatch,
        tmp_path,
        args=["--threshold", "4"],
        dry_run=False,
        payload=RuntimeError("Ops-Cli 执行失败 [AUTH_REQUIRED]：登录态失效"),
    )
    assert run.status == "failed"
    assert any("AUTH_REQUIRED" in err for err in run.errors)


def test_chinese_alias_resolves() -> None:
    assert resolve_task("猫超工单监控") == "tmcs_xp_workorder_watch"
    assert resolve_task("XP工单监控") == "tmcs_xp_workorder_watch"
    assert resolve_task("猫超XP工单数量") == "tmcs_xp_workorder_watch"
    assert resolve_task("猫超工单数量") == "tmcs_xp_workorder_watch"


def _step_outputs(runner: WorkflowRunner, step_id: str) -> dict:
    import json

    path = runner.last_run_dir / "steps" / f"{step_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["outputs"]
