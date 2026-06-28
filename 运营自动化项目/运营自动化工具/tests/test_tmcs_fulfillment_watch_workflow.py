from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from core.task_registry import resolve_task

from workflows.tmcs_fulfillment_watch import steps
from workflows.tmcs_fulfillment_watch.workflow import build_workflow


def _metrics(**overrides) -> dict:
    base = {
        "pickup_24h_rate": 99.43,
        "pickup_48h_rate": 100.0,
        "door_delivery_rate": 92.59,
        "next_day_delivery_rate": 58.0,
        "four_cp_rate": 99.47,
        "four_cp_rate_ex_remote": 99.46,
        "delivery_promise_rate": 100.0,
        "avg_pay_to_sign_hours": 55.9,
        "exception_feedback_required": False,
    }
    base.update(overrides)
    return base


def _payload(metrics: dict, *, weekly=None, dry_run: bool = False, simulated: bool = False) -> dict:
    return {
        "success": True,
        "platform": "tmcs",
        "command": "fulfillment overview",
        "data": {
            "metrics": metrics,
            "weekly_warning_level": weekly,
            "source": "simulated" if simulated else "page",
            "simulated": simulated,
            "scene": "tmall_chaoshi/fulfillment_overview",
            "dry_run": dry_run,
            "artifacts": [],
            "context_path": "/tmp/x.json",
        },
    }


def _run(monkeypatch, tmp_path: Path, args: list[str], *, dry_run: bool, payload):
    seen: list = []
    sent: list = []

    def fake_run_ops_json(command, interactive_recovery=None):
        seen.append((list(command), interactive_recovery))
        if isinstance(payload, Exception):
            raise payload
        return payload

    def fake_sender(content, msgtype="markdown"):
        sent.append((content, msgtype))
        return {"success": True}

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)
    # 包裹 send_notification 默认 sender，确保真实发送路径不触达真实 send_wecom。
    real_send = steps.send_notification

    def guarded_send(content, *, dry_run, msgtype="markdown", sender=None):
        return real_send(content, dry_run=dry_run, msgtype=msgtype, sender=sender or fake_sender)

    monkeypatch.setattr(steps, "send_notification", guarded_send)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": dry_run, "args": args}, dry_run=dry_run)
    return run, seen, sent, runner


def _step_outputs(runner: WorkflowRunner, step_id: str) -> dict:
    path = runner.last_run_dir / "steps" / f"{step_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["outputs"]


def _risk_metrics(runner) -> dict:
    out = _step_outputs(runner, "evaluate_metrics")
    return {item["metric"]: item for item in out["risk_items"]}


# 1. workflow 可以注册
def test_workflow_registers() -> None:
    wf = discover_workflow("tmcs_fulfillment_watch")
    assert wf.id == "tmcs_fulfillment_watch"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "fetch_fulfillment_overview",
        "evaluate_metrics",
        "build_warning_message",
        "notify_if_needed",
        "collect_outputs",
    ]


# 2. 中文入口可以解析
def test_chinese_alias_resolves() -> None:
    assert resolve_task("猫超履约监控") == "tmcs_fulfillment_watch"
    assert resolve_task("天猫超市履约监控") == "tmcs_fulfillment_watch"
    assert resolve_task("猫超物流履约") == "tmcs_fulfillment_watch"
    assert resolve_task("商家仓履约监控") == "tmcs_fulfillment_watch"


# 3. 24H支揽率低于 95 触发风险（fail）
def test_pickup_24h_below_threshold_fails(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=_payload(_metrics(pickup_24h_rate=93.0)),
    )
    assert run.status == "success"
    risks = _risk_metrics(runner)
    assert risks["pickup_24h_rate"]["severity"] == "fail"
    assert _step_outputs(runner, "evaluate_metrics")["should_notify"] is True


# 4. 24H支揽率接近 95 触发接近预警（near，margin 默认 2）
def test_pickup_24h_near_threshold_warns(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=["--warning-margin", "2"], dry_run=False,
        payload=_payload(_metrics(pickup_24h_rate=96.0)),
    )
    risks = _risk_metrics(runner)
    assert risks["pickup_24h_rate"]["severity"] == "near"


# 5. 送货上门实际达成率低于 75 触发风险
def test_door_delivery_below_threshold_fails(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=_payload(_metrics(door_delivery_rate=70.0)),
    )
    risks = _risk_metrics(runner)
    assert risks["door_delivery_rate"]["severity"] == "fail"


# 6. 隔日达率低于 55 触发风险
def test_next_day_below_threshold_fails(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=_payload(_metrics(next_day_delivery_rate=50.0)),
    )
    risks = _risk_metrics(runner)
    assert risks["next_day_delivery_rate"]["severity"] == "fail"


# 7. 48H支揽率不再触发风险，只保留前三个核心指标预警
def test_pickup_48h_is_not_alert_metric(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=_payload(_metrics(pickup_48h_rate=99.5)),
    )
    risks = _risk_metrics(runner)
    assert "pickup_48h_rate" not in risks


# 8. 4CP占比为观测/记录项：偏低不触发风险（按真实页面口径，4CP 无硬达标线）
def test_four_cp_is_record_only(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=_payload(_metrics(four_cp_rate=85.0, four_cp_rate_ex_remote=85.0)),
    )
    risks = _risk_metrics(runner)
    assert "four_cp_rate" not in risks
    assert "four_cp_rate_ex_remote" not in risks


# 9. 表达签准率不再触发风险，只保留前三个核心指标预警
def test_delivery_promise_is_not_alert_metric(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=_payload(_metrics(delivery_promise_rate=90.0)),
    )
    risks = _risk_metrics(runner)
    assert "delivery_promise_rate" not in risks


# 10. 全部正常时 should_notify=false，且不发送通知
def test_all_ok_no_notify(monkeypatch, tmp_path: Path) -> None:
    safe = _metrics(
        pickup_24h_rate=99.0,
        door_delivery_rate=90.0,
        next_day_delivery_rate=70.0,
        delivery_promise_rate=99.0,
    )
    run, _, sent, runner = _run(
        monkeypatch, tmp_path, args=["--notify"], dry_run=False,
        payload=_payload(safe),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "collect_outputs")
    assert out["should_notify"] is False
    assert out["risk_items"] == []
    assert sent == []
    assert out["notification"]["sent"] is False


# 11. dry-run 不发送真实通知（即便有风险且 --notify）
def test_dry_run_does_not_send(monkeypatch, tmp_path: Path) -> None:
    run, seen, sent, runner = _run(
        monkeypatch, tmp_path, args=["--simulate-risk", "--notify", "--dry-run"], dry_run=True,
        payload=_payload(_metrics(), dry_run=True, simulated=True),
    )
    assert run.status == "dry_run_success"
    command, interactive = seen[0]
    assert "--dry-run" in command
    assert interactive is False
    out = _step_outputs(runner, "collect_outputs")
    assert out["should_notify"] is True
    # 真实 send_wecom 绝不被触达：预览而非发送
    assert sent == []
    assert out["notification"]["sent"] is False
    assert out["notification"]["dry_run"] is True
    assert out["notification"]["preview"]


def test_dry_run_does_not_invoke_subprocess(monkeypatch, tmp_path: Path) -> None:
    import subprocess

    def boom(*args, **kwargs):
        raise AssertionError("dry-run 不应进入真实 subprocess")

    monkeypatch.setattr(subprocess, "run", boom)
    run, _, _, _ = _run(
        monkeypatch, tmp_path, args=["--dry-run"], dry_run=True,
        payload=_payload(_metrics(), dry_run=True, simulated=True),
    )
    assert run.status == "dry_run_success"


def test_exception_feedback_hidden_from_warning_message(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=["--notify"], dry_run=False,
        payload=_payload(
            _metrics(
                pickup_24h_rate=93.0,
                exception_feedback_required=True,
            ),
            weekly="B",
        ),
    )
    assert run.status == "success"
    out = _step_outputs(runner, "build_warning_message")
    message = out["warning_message"]
    assert "24H支揽率" in message
    assert "送货上门率" not in message
    assert "履约异常单反馈" not in message
    assert message.index("周数据预警等级：B 类") < message.index("24H支揽率")


def test_only_three_core_metrics_appear_in_warning_message(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=["--notify"], dry_run=False,
        payload=_payload(
            _metrics(
                pickup_24h_rate=93.0,
                door_delivery_rate=74.0,
                next_day_delivery_rate=54.0,
                pickup_48h_rate=99.5,
                delivery_promise_rate=90.0,
                exception_feedback_required=True,
            ),
            weekly="B",
        ),
    )
    assert run.status == "success"
    message = _step_outputs(runner, "build_warning_message")["warning_message"]
    assert "24H支揽率" in message
    assert "送货上门率" in message
    assert "隔日达率" in message
    assert "48H支揽率" not in message
    assert "表达签准率" not in message
    assert "履约异常单反馈" not in message


def test_exception_feedback_only_does_not_notify(monkeypatch, tmp_path: Path) -> None:
    run, _, sent, runner = _run(
        monkeypatch, tmp_path, args=["--notify"], dry_run=False,
        payload=_payload(_metrics(exception_feedback_required=True)),
    )
    assert run.status == "success"
    warning = _step_outputs(runner, "build_warning_message")
    out = _step_outputs(runner, "collect_outputs")
    assert warning["warning_message"] == ""
    assert out["should_notify"] is False
    assert sent == []


# 12. Ops-Cli JSON 结构正确（metrics/weekly/source 透传）
def test_ops_json_structure_passed_through(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=_payload(_metrics(), weekly="C"),
    )
    out = _step_outputs(runner, "collect_outputs")
    assert out["warning_level"] == "C"
    assert out["source"] == "page"
    assert set(out["metrics"]) == set(steps.METRIC_LABELS)
    # weekly 预警本身即风险来源
    risks = _risk_metrics(runner)
    assert "weekly_warning_level" in risks


# 13. Ops-Cli 失败时 workflow 输出清晰错误
def test_ops_failure_propagates(monkeypatch, tmp_path: Path) -> None:
    run, _, _, _ = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=RuntimeError("Ops-Cli 执行失败 [FULFILLMENT_OVERVIEW_NOT_FOUND]：尚未学习"),
    )
    assert run.status == "failed"
    assert any("FULFILLMENT_OVERVIEW_NOT_FOUND" in err for err in run.errors)


def test_negative_warning_margin_rejected(monkeypatch, tmp_path: Path) -> None:
    run, _, _, _ = _run(
        monkeypatch, tmp_path, args=["--warning-margin", "-1"], dry_run=False,
        payload=_payload(_metrics()),
    )
    assert run.status == "failed"


def test_exception_feedback_triggers_risk(monkeypatch, tmp_path: Path) -> None:
    run, _, _, runner = _run(
        monkeypatch, tmp_path, args=[], dry_run=False,
        payload=_payload(_metrics(exception_feedback_required=True)),
    )
    risks = _risk_metrics(runner)
    assert risks["exception_feedback_required"]["severity"] == "action"
