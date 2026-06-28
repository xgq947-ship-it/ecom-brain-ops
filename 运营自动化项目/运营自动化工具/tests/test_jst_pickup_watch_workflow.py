from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.jst_pickup_watch import risk_evaluator, steps
from workflows.jst_pickup_watch.workflow import build_workflow


def _config() -> dict:
    return {
        "pickup_watch": {
            "hours": 48,
            "risk_thresholds": {
                "normal_reminder_hours": 12,
                "high_risk_hours": 20,
                "timeout_hours": 24,
            },
            "warehouse": {"stop_shipping_time": "17:30"},
        },
        "platform_rules": {
            "cat_supermarket": {
                "enabled": True,
                "pay_time_offset_minutes": 30,
                "after_1730_orders_next_day": True,
            }
        },
    }


def _abnormal_payload(command, interactive_recovery):
    return {
        "success": True,
        "data": {
            "checked_at": "2026-05-28T10:00:00+08:00",
            "orders": [
                {
                    "platform": "天猫超市",
                    "platform_order_no": "P-TIMEOUT",
                    "jst_order_no": "J-TIMEOUT",
                    "jst_pay_time": "2026-05-27T08:00:00+08:00",
                    "has_pickup_record": False,
                }
            ],
        },
    }


def test_workflow_registers() -> None:
    wf = discover_workflow("jst_pickup_watch")
    assert wf.id == "jst_pickup_watch"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "load_config",
        "fetch_pickup_watch_data",
        "analyze_abnormal_orders",
        "notify_if_needed",
        "collect_outputs",
    ]


def test_dry_run_does_not_send_and_writes_runs(monkeypatch, tmp_path: Path) -> None:
    sent: list = []
    monkeypatch.setattr(risk_evaluator, "load_config", _config)
    monkeypatch.setattr(steps, "run_ops_json", _abnormal_payload)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)

    assert run.status == "dry_run_success"
    assert sent == []  # dry-run 绝不发送真实微信

    run_json = json.loads((runner.last_run_dir / "run.json").read_text(encoding="utf-8"))
    step_ids = {s["step_id"]: s["status"] for s in run_json["steps"]}
    assert step_ids["notify_if_needed"] == "dry_run_success"
    notify_step = json.loads((runner.last_run_dir / "steps" / "notify_if_needed.json").read_text(encoding="utf-8"))
    assert notify_step["outputs"]["notification"]["sent"] is False
    assert "preview" in notify_step["outputs"]["notification"]


def test_dry_run_passes_dry_run_flag_to_ops(monkeypatch, tmp_path: Path) -> None:
    seen_commands: list = []

    def capture(command, interactive_recovery):
        seen_commands.append((list(command), interactive_recovery))
        return {"success": True, "data": {"checked_at": "2026-05-28T10:00:00+08:00", "orders": []}}

    monkeypatch.setattr(risk_evaluator, "load_config", _config)
    monkeypatch.setattr(steps, "run_ops_json", capture)

    runner = WorkflowRunner(tmp_path)
    runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--hours", "48", "--dry-run"]}, dry_run=True)

    command, interactive = seen_commands[0]
    assert "--dry-run" in command
    assert interactive is False
    assert "48" in command


def test_real_notify_sends_when_abnormal(monkeypatch, tmp_path: Path) -> None:
    sent: list = []
    monkeypatch.setattr(risk_evaluator, "load_config", _config)
    monkeypatch.setattr(steps, "run_ops_json", _abnormal_payload)
    # 通过通知后端注册表替换默认 wecom 后端，拦截真实发送。
    from core.runtime import notify as notify_module

    monkeypatch.setitem(
        notify_module._SENDER_FACTORIES,
        "wecom",
        lambda: (lambda content, msgtype="text": sent.append((content, msgtype)) or {"success": True, "sent": True}),
    )

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": False, "args": ["--notify"]}, dry_run=False)

    assert run.status == "success"
    assert len(sent) == 1
    assert sent[0][1] == "markdown"
