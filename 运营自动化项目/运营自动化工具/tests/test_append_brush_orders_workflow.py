from __future__ import annotations

from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.append_brush_orders import appender
from workflows.append_brush_orders import steps
from workflows.append_brush_orders.workflow import build_workflow


def _common(monkeypatch, run_calls: list):
    monkeypatch.setattr(appender, "configure_paths", lambda **k: None)
    monkeypatch.setattr(appender, "has_xlsx_files", lambda d: False)

    def fake_run(dry_run=False, *, auto_fetch_wechat=True, wechat_month_day=None, print_skipped_wechat=False):
        run_calls.append({"dry_run": dry_run, "auto_fetch_wechat": auto_fetch_wechat})
        return {
            "appended_orders": [] if dry_run else ["O1"],
            "appended_count": 0 if dry_run else 1,
            "source_dir": "/tmp/src",
            "latest_brush_orders_path": "/tmp/latest.json",
        }

    monkeypatch.setattr(appender, "run", fake_run)


def test_workflow_registers() -> None:
    wf = discover_workflow("append_brush_orders")
    assert wf.id == "append_brush_orders"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "load_source_orders",
        "validate_orders",
        "append_to_register",
        "collect_artifacts",
    ]


def test_dry_run_is_readonly(monkeypatch, tmp_path: Path) -> None:
    run_calls: list = []
    _common(monkeypatch, run_calls)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)

    assert run.status == "dry_run_success"
    assert len(run_calls) == 1
    assert run_calls[0]["dry_run"] is True
    assert run_calls[0]["auto_fetch_wechat"] is True  # dry-run 允许扫描微信文件，但不复制不写入


def test_real_run_invokes_appender_run(monkeypatch, tmp_path: Path) -> None:
    run_calls: list = []
    _common(monkeypatch, run_calls)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": False, "args": []}, dry_run=False)

    assert run.status == "success"
    assert run_calls[0]["dry_run"] is False
    assert run_calls[0]["auto_fetch_wechat"] is True
