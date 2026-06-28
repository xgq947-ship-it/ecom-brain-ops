from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.jst_order_invoice_workorder import steps
from workflows.jst_order_invoice_workorder.workflow import build_workflow

_BASE_ARGS = [
    "--outer-order-id", "5111330689403040244",
    "--shop-name", "奥克斯索隆专卖店",
    "--invoice-entity", "福安市索隆电子有限公司",
    "--title", "测试公司",
    "--tax-no", "91330000000000000X",
    "--address", "浙江省杭州市",
    "--phone", "0571-12345678",
    "--bank", "中国银行",
    "--bank-account", "12345678901",
    "--amount", "100.00",
]

_FAKE_RESOLVE_PAYLOAD = {
    "success": True,
    "platform": "jst",
    "command": "order invoice",
    "data": {
        "order_id": "5111330689403040244",
        "internal_order_id": "10001",
        "online_order_id": "LP10001",
        "matched_filter": "outer_so_id",
        "invoice_type": "专用发票",
        "amount": 100.0,
        "submitted": False,
    },
}

_FAKE_SUBMIT_PAYLOAD = {
    "success": True,
    "platform": "jst",
    "command": "order invoice",
    "data": {
        "order_id": "5111330689403040244",
        "internal_order_id": "10001",
        "online_order_id": "LP10001",
        "invoice_type": "专用发票",
        "amount": 100.0,
        "submitted": True,
        "result": {"success": True},
    },
}


def _make_fake_run_ops(resolve_payload, submit_payload, calls: list):
    def fake_run_ops_json(args, *, interactive_recovery=None):
        calls.append(list(args))
        if "--execute" in args:
            return submit_payload
        return resolve_payload

    return fake_run_ops_json


def test_workflow_registers() -> None:
    wf = discover_workflow("jst_order_invoice_workorder")
    assert wf.id == "jst_order_invoice_workorder"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "resolve_order",
        "submit_workorder",
        "collect_outputs",
    ]


def test_dry_run_skips_submit(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    monkeypatch.setattr(steps, "run_ops_json", _make_fake_run_ops(_FAKE_RESOLVE_PAYLOAD, _FAKE_SUBMIT_PAYLOAD, calls))

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": ["--dry-run", *_BASE_ARGS]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    # resolve_order 调了一次（无 --execute），submit 未调用
    assert len(calls) == 1
    assert "--execute" not in calls[0]

    submit_step = json.loads((runner.last_run_dir / "steps" / "submit_workorder.json").read_text(encoding="utf-8"))
    assert submit_step["outputs"]["skipped"] is True


def test_preview_without_execute_skips_submit(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    monkeypatch.setattr(steps, "run_ops_json", _make_fake_run_ops(_FAKE_RESOLVE_PAYLOAD, _FAKE_SUBMIT_PAYLOAD, calls))

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": _BASE_ARGS},
        dry_run=False,
    )

    assert run.status == "success"
    assert len(calls) == 1
    assert "--execute" not in calls[0]

    submit_step = json.loads((runner.last_run_dir / "steps" / "submit_workorder.json").read_text(encoding="utf-8"))
    assert submit_step["outputs"]["skipped"] is True
    assert "execute" in submit_step["outputs"]["reason"]


def test_execute_submits_workorder(monkeypatch, tmp_path: Path) -> None:
    calls: list = []
    monkeypatch.setattr(steps, "run_ops_json", _make_fake_run_ops(_FAKE_RESOLVE_PAYLOAD, _FAKE_SUBMIT_PAYLOAD, calls))

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": [*_BASE_ARGS, "--execute"]},
        dry_run=False,
    )

    assert run.status == "success"
    # resolve 调一次（无 --execute），submit 调一次（有 --execute）
    assert len(calls) == 2
    assert "--execute" not in calls[0]
    assert "--execute" in calls[1]

    submit_step = json.loads((runner.last_run_dir / "steps" / "submit_workorder.json").read_text(encoding="utf-8"))
    assert submit_step["outputs"]["submitted"] is True


def test_check_inputs_fails_on_missing_order_id(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "args": [
                # 故意不传 --order-id 或 --outer-order-id
                "--title", "测试", "--tax-no", "91X", "--address", "杭州",
                "--phone", "0571", "--bank", "中行", "--bank-account", "123", "--amount", "100",
            ]
        },
        dry_run=False,
    )
    assert run.status == "failed"
    assert any("订单号" in e for e in run.errors)


def test_check_inputs_fails_on_missing_required_field(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "args": [
                "--outer-order-id", "5118069602223015134",
                # 故意不传 --title
                "--shop-name", "奥克斯索隆专卖店", "--invoice-entity", "福安市索隆电子有限公司", "--tax-no", "91X", "--address", "杭州",
                "--phone", "0571", "--bank", "中行", "--bank-account", "123", "--amount", "100",
            ]
        },
        dry_run=False,
    )
    assert run.status == "failed"
    assert any("title" in e for e in run.errors)


def test_check_inputs_fails_on_missing_shop_name(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "args": [
                "--outer-order-id", "5111330689403040244",
                "--invoice-entity", "福安市索隆电子有限公司",
                "--title", "测试公司",
                "--tax-no", "91X",
                "--address", "杭州",
                "--phone", "0571",
                "--bank", "中行",
                "--bank-account", "123",
                "--amount", "100",
            ]
        },
        dry_run=False,
    )
    assert run.status == "failed"
    assert any("shop_name" in e for e in run.errors)


def test_check_inputs_fails_on_invalid_amount(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    # _BASE_ARGS 末尾两项是 ["--amount", "100.00"]，去掉两项再替换为非法值
    run = runner.run(
        build_workflow(),
        inputs={"args": [*_BASE_ARGS[:-2], "--amount", "abc"]},
        dry_run=False,
    )
    assert run.status == "failed"


def test_dry_run_survives_ops_failure(monkeypatch, tmp_path: Path) -> None:
    def failing_ops(args, *, interactive_recovery=None):
        raise RuntimeError("平台不可达（模拟）")

    monkeypatch.setattr(steps, "run_ops_json", failing_ops)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"args": ["--dry-run", *_BASE_ARGS]},
        dry_run=True,
    )
    # dry-run 下平台调用失败应安全降级为 skip，整体不崩溃
    assert run.status == "dry_run_success"
    resolve_step = json.loads((runner.last_run_dir / "steps" / "resolve_order.json").read_text(encoding="utf-8"))
    assert resolve_step["outputs"]["skipped"] is True
