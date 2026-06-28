from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.jst_brush_reimburse_workorder import reimburse
from workflows.jst_brush_reimburse_workorder import steps
from workflows.jst_brush_reimburse_workorder.workflow import build_workflow


def _fake_batch(workbook_path: Path) -> reimburse.BatchInfo:
    order = reimburse.BatchOrder(
        row_index=3,
        brusher="张三",
        brush_date="2026-05-01",
        order_no="ORDER001",
        order_amount=Decimal("100"),
        commission_amount=Decimal("10"),
        product_code="P001",
        product_name="商品A",
    )
    return reimburse.BatchInfo(
        workbook_path=workbook_path,
        start_row=3,
        end_row=3,
        orders=[order],
        principal_total=Decimal("100"),
        payout_total=Decimal("10"),
    )


def _setup_common(monkeypatch, tmp_path: Path, submit_calls: list, danger: dict):
    register = tmp_path / "register.xlsx"
    register.write_bytes(b"PK")  # 仅需存在

    monkeypatch.setattr(reimburse, "read_current_batch", lambda p: _fake_batch(register))

    def fake_payload(batch, order, *, execute=False, interactive_recovery=False):
        if execute:
            submit_calls.append(order.order_no)
            return {"submitted": True, "has_existing_workorder": False, "upload_url": "u://x", "result": "ok"}
        return {
            "internal_order_id": "OID1",
            "online_order_id": "LP1",
            "item_name": "商品A",
            "has_existing_workorder": False,
        }

    monkeypatch.setattr(reimburse, "ops_reimburse_payload", fake_payload)
    monkeypatch.setattr(reimburse, "backup_workbook", lambda p: danger.__setitem__("backup", danger.get("backup", 0) + 1) or (tmp_path / "bak.xlsx"))
    monkeypatch.setattr(reimburse, "write_marker_row", lambda b: danger.__setitem__("marker", danger.get("marker", 0) + 1) or 4)
    monkeypatch.setattr(reimburse, "write_failed_export", lambda rows: None)
    return register


def test_workflow_registers() -> None:
    wf = discover_workflow("jst_brush_reimburse_workorder")
    assert wf.id == "jst_brush_reimburse_workorder"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "load_reimburse_data",
        "validate_amounts",
        "preview_workorder",
        "submit_workorder",
        "update_register",
        "collect_artifacts",
    ]


def test_dry_run_does_not_submit_or_touch_register(monkeypatch, tmp_path: Path) -> None:
    submit_calls: list = []
    danger: dict = {}
    register = _setup_common(monkeypatch, tmp_path, submit_calls, danger)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--input", str(register)]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert submit_calls == []           # 未提交真实工单
    assert danger.get("backup", 0) == 0  # 未备份登记表
    assert danger.get("marker", 0) == 0  # 未写标记行（Excel 结构零改写）

    submit_step = json.loads((runner.last_run_dir / "steps" / "submit_workorder.json").read_text(encoding="utf-8"))
    register_step = json.loads((runner.last_run_dir / "steps" / "update_register.json").read_text(encoding="utf-8"))
    assert submit_step["outputs"]["skipped"] is True
    assert register_step["outputs"]["skipped"] is True


def test_real_run_submits_and_writes_marker(monkeypatch, tmp_path: Path) -> None:
    submit_calls: list = []
    danger: dict = {}
    register = _setup_common(monkeypatch, tmp_path, submit_calls, danger)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--input", str(register)]},
        dry_run=False,
    )

    assert run.status == "success"
    assert submit_calls == ["ORDER001"]   # 提交了工单
    assert danger.get("backup", 0) == 1    # 备份一次
    assert danger.get("marker", 0) == 1    # 写标记行一次


def test_dry_run_missing_register_is_safe(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "nope.xlsx"
    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--input", str(missing)]},
        dry_run=True,
    )
    assert run.status == "dry_run_success"  # 文件缺失时安全预览，不崩溃
