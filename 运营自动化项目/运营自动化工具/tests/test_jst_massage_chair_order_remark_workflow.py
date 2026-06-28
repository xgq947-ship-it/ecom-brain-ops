from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.jst_massage_chair_order_remark import steps
from workflows.jst_massage_chair_order_remark.workflow import build_workflow


def _ops_result(payload: dict) -> steps.OpsCommandResult:
    return steps.OpsCommandResult.from_payload(payload)


def _source_file(tmp_path: Path) -> Path:
    path = tmp_path / "按摩椅资料表.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["商品编码", "商品名称"])
    worksheet.append(["AMY001", "按摩椅一号"])
    worksheet.append(["AMY002", "按摩椅二号"])
    workbook.save(path)
    return path


def _orders() -> list[dict]:
    return [
        {
            "order_id": "OID1",
            "outer_order_id": "TB1",
            "shop_name": steps.DEFAULT_SHOP_NAME,
            "status": steps.DEFAULT_STATUS,
            "pay_time": "2026-06-02 10:00:00",
            "remark": "",
            "items": [{"product_code": "AMY001", "product_name": "按摩椅 A"}],
        },
        {
            "order_id": "OID2",
            "remark": "已有备注",
            "items": [{"product_code": "AMY001", "product_name": "按摩椅 A"}],
        },
        {
            "order_id": "OID3",
            "remark": "",
            "items": [{"product_code": "", "product_name": "按摩椅 B"}],
        },
        {
            "order_id": "OID4",
            "remark": "",
            "items": [{"product_code": "UNKNOWN", "product_name": "按摩椅 C"}],
        },
        {
            "order_id": "OID5",
            "remark": "",
            "items": [
                {"product_code": "AMY001", "product_name": "按摩椅 A"},
                {"product_code": "AMY002", "product_name": "按摩椅 B"},
            ],
        },
    ]


def test_workflow_registers() -> None:
    wf = discover_workflow("jst_massage_chair_order_remark")
    assert wf.id == "jst_massage_chair_order_remark"
    assert [step.id for step in wf.steps] == [
        "check_inputs",
        "fetch_orders",
        "load_massage_chair_mapping",
        "build_remark_plan",
        "apply_remarks",
        "normalize_abnormal_orders",
        "collect_outputs",
    ]


def test_defaults_and_dry_run_never_executes(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], bool | None]] = []
    source = _source_file(tmp_path)
    monkeypatch.setattr(steps, "get_path", lambda name: tmp_path)

    def fake_run_ops_command(command, interactive_recovery=None):
        calls.append((list(command), interactive_recovery))
        return _ops_result({"success": True, "data": {"orders": _orders(), "filters": {}}})

    monkeypatch.setattr(steps, "run_ops_command", fake_run_ops_command)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--source-file", str(source)]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert len(calls) == 2
    query_command, interactive = calls[0]
    assert query_command[:3] == ["jst", "order", "query"]
    assert "--execute" not in query_command
    assert interactive is False
    assert steps.DEFAULT_SHOP_NAME in query_command
    assert steps.DEFAULT_STATUS in query_command
    assert steps.DEFAULT_KEYWORD in query_command
    queried_dates = [call[0][call[0].index("--date") + 1] for call in calls]
    assert queried_dates == steps._default_query_dates()

    plan = run.outputs["remark_plan"]
    assert plan[0]["action"] == "remark"
    assert plan[0]["remark_text"] == "按摩椅一号"
    assert plan[1]["reason"] == "already_has_remark"
    assert plan[2]["reason"] == "missing_product_code"
    assert plan[3]["reason"] == "product_code_not_found"
    assert plan[4]["remark_text"] == "按摩椅一号、按摩椅二号"
    assert run.outputs["to_remark_count"] == 2
    assert run.outputs["executed_count"] == 0

    apply_step = json.loads((runner.last_run_dir / "steps" / "apply_remarks.json").read_text(encoding="utf-8"))
    assert apply_step["outputs"]["skipped"] is True


def test_default_source_file_uses_massage_chair_mapping_config(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    source = _source_file(tmp_path)
    wrong_title_library = tmp_path / "按摩器材爆款标题库.xlsx"

    def fake_get_path(name: str) -> Path:
        if name == "massage_chair_mapping_file":
            return source
        if name == "massage_title_library_file":
            return wrong_title_library
        return tmp_path

    def fake_run_ops_command(command, interactive_recovery=None):
        calls.append(list(command))
        return _ops_result({"success": True, "data": {"orders": [_orders()[0]], "filters": {}}})

    monkeypatch.setattr(steps, "get_path", fake_get_path)
    monkeypatch.setattr(steps, "run_ops_command", fake_run_ops_command)

    run = WorkflowRunner(tmp_path / "runs").run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run"]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert run.outputs["source_file"] == str(source.resolve())
    assert calls


def test_workflow_passes_order_id_and_shipped_status(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    source = _source_file(tmp_path)
    monkeypatch.setattr(steps, "get_path", lambda name: tmp_path)

    def fake_run_ops_command(command, interactive_recovery=None):
        calls.append(list(command))
        return _ops_result({"success": True, "data": {"orders": [_orders()[0]], "filters": {}}})

    monkeypatch.setattr(steps, "run_ops_command", fake_run_ops_command)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": True,
            "args": [
                "--dry-run",
                "--order-id",
                "LP00820708449401",
                "--status",
                "线上已发货",
                "--source-file",
                str(source),
            ],
        },
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    query_command = calls[0]
    assert "--order-id" in query_command
    assert "LP00820708449401" in query_command
    assert "--status" in query_command
    assert "线上已发货" in query_command


def test_explicit_date_keeps_single_day_query(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    source = _source_file(tmp_path)
    monkeypatch.setattr(steps, "get_path", lambda name: tmp_path)

    def fake_run_ops_command(command, interactive_recovery=None):
        calls.append(list(command))
        return _ops_result({"success": True, "data": {"orders": [_orders()[0]], "filters": {"date": "2026-06-03"}}})

    monkeypatch.setattr(steps, "run_ops_command", fake_run_ops_command)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--date", "2026-06-03", "--source-file", str(source)]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert len(calls) == 1
    assert calls[0][calls[0].index("--date") + 1] == "2026-06-03"


def test_execute_normalizes_only_remarked_abnormal_orders(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    source = _source_file(tmp_path)
    monkeypatch.setattr(steps, "get_path", lambda name: tmp_path)

    abnormal = {
        "order_id": "ABN1",
        "status": "异常",
        "remark": "",
        "items": [{"product_code": "AMY001", "product_name": "按摩椅 A"}],
    }
    normal = {
        "order_id": "NOR1",
        "status": "已付款待审核",
        "remark": "",
        "items": [{"product_code": "AMY002", "product_name": "按摩椅 B"}],
    }

    def fake_run_ops_command(command, interactive_recovery=None):
        calls.append(list(command))
        if command[:3] == ["jst", "order", "query"]:
            return _ops_result({"success": True, "data": {"orders": [abnormal, normal], "filters": {}}})
        # remark / normalize 都返回成功
        return _ops_result({"success": True, "data": {"summary": {"success": 1}}})

    monkeypatch.setattr(steps, "run_ops_command", fake_run_ops_command)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--execute", "--source-file", str(source)]},
        dry_run=False,
    )

    assert run.status == "success"
    normalize_calls = [c for c in calls if c[:3] == ["jst", "order", "normalize"]]
    # 只对「异常 + 成功备注」的 ABN1 转正常，NOR1 不转
    assert len(normalize_calls) == 1
    assert "ABN1" in normalize_calls[0]
    assert "--execute" in normalize_calls[0]
    assert all("NOR1" not in c for c in normalize_calls)
    assert run.outputs["abnormal_remarked_count"] == 1
    assert run.outputs["normalized_count"] == 1
    assert run.outputs["normalize_failed_count"] == 0


def test_execute_marks_workflow_failed_when_any_remark_fails(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    source = _source_file(tmp_path)
    monkeypatch.setattr(steps, "get_path", lambda name: tmp_path)

    def fake_run_ops_command(command, interactive_recovery=None):
        calls.append(list(command))
        if command[:3] == ["jst", "order", "query"]:
            return _ops_result({"success": True, "data": {"orders": [_orders()[0], _orders()[4]], "filters": {}}})
        if "OID5" in command:
            raise RuntimeError("备注失败")
        return _ops_result({"success": True, "data": {"summary": {"success": 1}}})

    monkeypatch.setattr(steps, "run_ops_command", fake_run_ops_command)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--execute", "--source-file", str(source)]},
        dry_run=False,
    )

    assert run.status == "failed"
    assert any("备注失败" in err for err in run.errors)
    remark_calls = [call for call in calls if call[:3] == ["jst", "order", "remark"]]
    assert len(remark_calls) == 2
    assert all("--execute" in call for call in remark_calls)
    assert run.outputs["executed_count"] == 1
    assert run.outputs["failed_count"] == 1
