"""run.py × NotchFlow 接线测试：dry-run 必须把 dry_run=True 透传给 reporter（不上报）。"""

from __future__ import annotations

import core.runtime.registry as registry
import run as run_mod
from core.runtime import build_workflow, step, success_result


class _Ctx:
    def __init__(self, *a, **k):
        pass

    def add_input(self, *a, **k):
        pass

    def add_output(self, *a, **k):
        pass

    def add_artifact(self, *a, **k):
        pass

    def add_error(self, *a, **k):
        pass

    def finish(self, *a, **k):
        return "/tmp/_ctx.json"


class _SpyReporter:
    def __init__(self):
        self.calls = []

    def start(self, workflow_id, task_name, message="开始执行", *, dry_run=False):
        self.calls.append(("start", dry_run))

    def success(self, workflow_id, task_name, message="执行完成", *, dry_run=False):
        self.calls.append(("success", dry_run))

    def failed(self, workflow_id, task_name, error="执行失败", *, dry_run=False):
        self.calls.append(("failed", dry_run))

    def waiting(self, workflow_id, task_name, message="等待人工处理", *, dry_run=False):
        self.calls.append(("waiting", dry_run))


def _wire(monkeypatch, tmp_path):
    monkeypatch.setattr(run_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_mod, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(run_mod, "TaskContext", _Ctx)


def test_dry_run_passes_dry_run_flag_to_reporter(monkeypatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path)
    spy = _SpyReporter()
    monkeypatch.setattr(run_mod, "notchflow", spy)
    wf = build_workflow("demo", "演示", [step("noop", "空步骤", lambda ctx: success_result())])
    monkeypatch.setattr(registry, "discover_workflow", lambda _id: wf)

    rc = run_mod.run_workflow(["demo", "--dry-run"])

    assert rc == 0
    # dry-run 下 start 与 success 都应被调用，且 dry_run=True（reporter 内部据此跳过实际写盘）。
    assert ("start", True) in spy.calls
    assert ("success", True) in spy.calls
    assert all(dry_run is True for _, dry_run in spy.calls)


def test_real_run_reports_with_dry_run_false(monkeypatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path)
    spy = _SpyReporter()
    monkeypatch.setattr(run_mod, "notchflow", spy)
    wf = build_workflow("demo", "演示", [step("noop", "空步骤", lambda ctx: success_result())])
    monkeypatch.setattr(registry, "discover_workflow", lambda _id: wf)

    rc = run_mod.run_workflow(["demo"])

    assert rc == 0
    assert ("start", False) in spy.calls
    assert ("success", False) in spy.calls


def test_nf_disable_replaces_reporter_with_noop(monkeypatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path)
    spy = _SpyReporter()
    monkeypatch.setattr(run_mod, "notchflow", spy)
    monkeypatch.setenv("NF_DISABLE", "1")
    wf = build_workflow("demo", "演示", [step("noop", "空步骤", lambda ctx: success_result())])
    monkeypatch.setattr(registry, "discover_workflow", lambda _id: wf)

    rc = run_mod.run_workflow(["demo"])

    assert rc == 0
    assert spy.calls == []
