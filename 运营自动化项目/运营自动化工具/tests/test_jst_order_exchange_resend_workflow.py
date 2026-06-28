from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from core.task_registry import resolve_task
from workflows.jst_order_exchange_resend import steps
from workflows.jst_order_exchange_resend.workflow import build_workflow

_ORDER_NO = "TEST123"


def _preview_payload(*, found=True, eligible=True, reason=None, sku_matched=None):
    return {
        "success": True,
        "platform": "jst",
        "command": "order exchange-resend preview",
        "data": {
            "order_no": _ORDER_NO,
            "mode": "resend",
            "action": "preview",
            "found_order": found,
            "matched_filter": "outer_so_id",
            "order_status": "线上已发货" if found else None,
            "eligible": eligible,
            "ineligible_reason": reason,
            "sku_matched": sku_matched,
            "final_payload": {"action": "resend", "internal_order_id": "10001"} if found else {},
            "pending_confirmation": [],
        },
    }


def _learn_payload():
    return {
        "success": True,
        "platform": "jst",
        "command": "order exchange-resend learn",
        "data": {
            "order_no": _ORDER_NO,
            "mode": "resend",
            "action": "learn",
            "found_order": True,
            "order_status": "线上已发货",
            "eligible": True,
            "steps_detected": [{"stage": "order_list", "keyword": "补发", "found": True, "match_count": 1}],
            "screenshot_paths": ["/tmp/01.png"],
            "profile_path": "/tmp/profile.json",
            "submitted": False,
        },
    }


def _submit_payload():
    return {
        "success": True,
        "platform": "jst",
        "command": "order exchange-resend submit",
        "data": {
            "order_no": _ORDER_NO,
            "mode": "resend",
            "action": "submit",
            "found_order": True,
            "order_status": "线上已发货",
            "eligible": True,
            "submitted": False,
            "final_payload": {"action": "resend", "internal_order_id": "10001"},
            "pending_confirmation": ["页面路径未确认"],
        },
    }


def _fake_ops(calls: list, *, preview, learn=None, submit=None):
    def fake_run_ops_json(args, *, interactive_recovery=None):
        calls.append(list(args))
        if "learn" in args:
            return learn or _learn_payload()
        if "submit" in args:
            return submit or _submit_payload()
        return preview

    return fake_run_ops_json


# --------------------------------------------------------------------------- #
def test_workflow_registers() -> None:
    wf = discover_workflow("jst_order_exchange_resend")
    assert wf.id == "jst_order_exchange_resend"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "inspect_existing_capabilities",
        "learn_or_preview_flow",
        "validate_eligibility",
        "submit_if_execute",
        "collect_outputs",
    ]


def test_chinese_entry_resolves() -> None:
    # 中文入口 / 英文 alias 都收敛到同一 canonical task name
    canonical = "聚水潭订单换货补发"
    assert resolve_task("聚水潭订单换货补发") == canonical
    assert resolve_task("聚水潭补发") == canonical
    assert resolve_task("聚水潭换货") == canonical
    assert resolve_task("jst_order_exchange_resend") == canonical


def test_missing_order_no_fails(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"args": ["--mode", "resend"]}, dry_run=False)
    assert run.status == "failed"
    assert any("订单号" in e for e in run.errors)


def test_invalid_mode_fails(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(), inputs={"args": ["--order-no", _ORDER_NO, "--mode", "foo"]}, dry_run=False
    )
    assert run.status == "failed"
    assert any("mode" in e or "resend" in e for e in run.errors)


def test_execute_without_confirm_fails(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": ["--order-no", _ORDER_NO, "--mode", "resend", "--execute"]},
        dry_run=False,
    )
    assert run.status == "failed"
    assert any("confirm-order-no" in e for e in run.errors)


def test_confirm_mismatch_fails(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "args": [
                "--order-no", _ORDER_NO, "--mode", "resend",
                "--execute", "--confirm-order-no", "OTHER",
            ]
        },
        dry_run=False,
    )
    assert run.status == "failed"
    assert any("不一致" in e for e in run.errors)


def test_dry_run_skips_submit(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    monkeypatch.setattr(steps, "run_ops_json", _fake_ops(calls, preview=_preview_payload()))

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": ["--dry-run", "--order-no", _ORDER_NO, "--mode", "resend"]},
        dry_run=True,
    )
    assert run.status == "dry_run_success"
    # 只调用 preview，绝不调用 submit
    assert all("--execute" not in c for c in calls)
    submit_step = json.loads((runner.last_run_dir / "steps" / "submit_if_execute.json").read_text(encoding="utf-8"))
    assert submit_step["outputs"]["skipped"] is True


def test_learn_only_does_not_submit(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    monkeypatch.setattr(steps, "run_ops_json", _fake_ops(calls, preview=_preview_payload()))

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": ["--order-no", _ORDER_NO, "--mode", "resend", "--learn-only"]},
        dry_run=False,
    )
    assert run.status == "success"
    assert any("learn" in c for c in calls)
    assert all("--execute" not in c for c in calls)
    submit_step = json.loads((runner.last_run_dir / "steps" / "submit_if_execute.json").read_text(encoding="utf-8"))
    assert submit_step["outputs"]["skipped"] is True


def test_execute_calls_submit(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    monkeypatch.setattr(steps, "run_ops_json", _fake_ops(calls, preview=_preview_payload()))

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "args": [
                "--order-no", _ORDER_NO, "--mode", "resend",
                "--execute", "--confirm-order-no", _ORDER_NO,
            ]
        },
        dry_run=False,
    )
    assert run.status == "success"
    submit_calls = [c for c in calls if "submit" in c]
    assert len(submit_calls) == 1
    assert "--execute" in submit_calls[0]
    collect = json.loads((runner.last_run_dir / "steps" / "collect_outputs.json").read_text(encoding="utf-8"))
    # 页面路径未确认：submitted=False 且输出了 final_payload
    assert collect["outputs"]["submitted"] is False
    assert collect["outputs"]["final_payload"]


def test_order_not_found_stops(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    monkeypatch.setattr(
        steps, "run_ops_json", _fake_ops(calls, preview=_preview_payload(found=False, eligible=False, reason="聚水潭未找到该订单"))
    )

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": ["--order-no", _ORDER_NO, "--mode", "resend"]},
        dry_run=False,
    )
    assert run.status == "failed"
    assert any("未找到订单" in e for e in run.errors)


def test_ineligible_stops(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    monkeypatch.setattr(
        steps,
        "run_ops_json",
        _fake_ops(calls, preview=_preview_payload(eligible=False, reason="订单状态「已取消」不允许补发")),
    )

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": ["--order-no", _ORDER_NO, "--mode", "exchange"]},
        dry_run=False,
    )
    assert run.status == "failed"
    assert any("不允许" in e for e in run.errors)


def test_dry_run_survives_ops_failure(monkeypatch, tmp_path: Path) -> None:
    def failing_ops(args, *, interactive_recovery=None):
        raise RuntimeError("平台不可达（模拟）")

    monkeypatch.setattr(steps, "run_ops_json", failing_ops)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": ["--dry-run", "--order-no", _ORDER_NO, "--mode", "resend"]},
        dry_run=True,
    )
    assert run.status == "dry_run_success"
    inspect_step = json.loads(
        (runner.last_run_dir / "steps" / "inspect_existing_capabilities.json").read_text(encoding="utf-8")
    )
    assert inspect_step["outputs"]["skipped"] is True
