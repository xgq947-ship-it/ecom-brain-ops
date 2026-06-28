from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.tmcs_sync_jst_shop_goods import steps
from workflows.tmcs_sync_jst_shop_goods.workflow import build_workflow


def test_workflow_registers() -> None:
    wf = discover_workflow("tmcs_sync_jst_shop_goods")
    assert wf.id == "tmcs_sync_jst_shop_goods"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "load_tmcs_goods",
        "query_tmcs_stock",
        "build_jst_import_excel",
        "import_jst_shop_goods",
        "collect_artifacts",
    ]


def test_dry_run_no_item_ids_is_safe(monkeypatch, tmp_path: Path) -> None:
    calls = {"query": 0, "build": 0, "import": 0}
    monkeypatch.setattr(steps.cli_client, "query_tmcs_stock", lambda **k: calls.__setitem__("query", calls["query"] + 1) or [])
    monkeypatch.setattr(steps.excel_builder, "build_import_workbooks", lambda **k: calls.__setitem__("build", calls["build"] + 1) or {})
    monkeypatch.setattr(steps.cli_client, "import_jst_shop_goods", lambda **k: calls.__setitem__("import", calls["import"] + 1) or {})

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)

    assert run.status == "dry_run_success"
    assert calls == {"query": 0, "build": 0, "import": 0}  # 零平台调用、零写入


def test_dry_run_with_item_ids_resolves_but_no_platform(monkeypatch, tmp_path: Path) -> None:
    calls = {"query": 0, "build": 0, "import": 0}
    monkeypatch.setattr(steps.cli_client, "query_tmcs_stock", lambda **k: calls.__setitem__("query", calls["query"] + 1) or [])
    monkeypatch.setattr(steps.excel_builder, "build_import_workbooks", lambda **k: calls.__setitem__("build", calls["build"] + 1) or {})
    monkeypatch.setattr(steps.cli_client, "import_jst_shop_goods", lambda **k: calls.__setitem__("import", calls["import"] + 1) or {})

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--item-ids", "1052305450766,1052305450766,234", "--dry-run"]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    load_step = json.loads((runner.last_run_dir / "steps" / "load_tmcs_goods.json").read_text(encoding="utf-8"))
    assert load_step["outputs"]["item_id_count"] == 2  # 去重
    assert calls == {"query": 0, "build": 0, "import": 0}


def test_real_run_without_import_builds_excel_only(monkeypatch, tmp_path: Path) -> None:
    import_calls: list = []
    monkeypatch.setattr(
        steps.cli_client,
        "query_tmcs_stock",
        lambda **k: [{"platform_item_id": "1052305450766", "platform_sku_id": "S", "supplier_goods_id": "SUP", "merchant_goods_code": "MGC"}],
    )
    fake_wb = {"import_path": tmp_path / "import.xlsx", "failed_path": None, "import_rows": 1, "failed_rows": 0}
    monkeypatch.setattr(steps.excel_builder, "build_import_workbooks", lambda **k: fake_wb)
    monkeypatch.setattr(
        steps.excel_builder,
        "build_correspondence_workbook",
        lambda **k: {
            "correspondence_path": str(tmp_path / "猫超商品对应关系导入表.xlsx"),
            "correspondence_rows": 1,
            "matched_rows": 1,
        },
    )
    monkeypatch.setattr(steps.cli_client, "import_jst_shop_goods", lambda **k: import_calls.append(k) or {"success": True})

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--item-ids", "1052305450766"]},
        dry_run=False,
    )

    assert run.status == "success"
    assert import_calls == []  # 没有 --import-jst 不导入
    artifacts = json.loads((runner.last_run_dir / "artifacts.json").read_text(encoding="utf-8"))
    assert any(a["role"] == "import" and a["platform"] == "jst" for a in artifacts)
    assert any(a["role"] == "correspondence" for a in artifacts)  # 同步生成对应关系导入表


def test_real_run_with_import_flag_imports(monkeypatch, tmp_path: Path) -> None:
    import_calls: list = []
    monkeypatch.setattr(
        steps.cli_client,
        "query_tmcs_stock",
        lambda **k: [{"platform_item_id": "1052305450766", "platform_sku_id": "S", "supplier_goods_id": "SUP", "merchant_goods_code": "MGC"}],
    )
    fake_wb = {"import_path": tmp_path / "import.xlsx", "failed_path": None, "import_rows": 1, "failed_rows": 0}
    monkeypatch.setattr(steps.excel_builder, "build_import_workbooks", lambda **k: fake_wb)
    monkeypatch.setattr(
        steps.excel_builder,
        "build_correspondence_workbook",
        lambda **k: {
            "correspondence_path": str(tmp_path / "猫超商品对应关系导入表.xlsx"),
            "correspondence_rows": 1,
            "matched_rows": 1,
        },
    )
    monkeypatch.setattr(steps.cli_client, "import_jst_shop_goods", lambda **k: import_calls.append(k) or {"success": True})

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--item-ids", "1052305450766", "--import-jst", "--import-mode", "cover"]},
        dry_run=False,
    )

    assert run.status == "success"
    assert len(import_calls) == 1
    assert import_calls[0]["mode"] == "cover"
