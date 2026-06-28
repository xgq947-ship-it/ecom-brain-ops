from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.buyer_show import packager
from workflows.buyer_show import steps
from workflows.buyer_show.workflow import build_workflow


def _make_register(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """构造一张登记表：第1行标题占位，第2行表头，之后为数据行。

    rows 每项为 (订单编号, 名称, 买家秀是否自动生成)。
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["登记表"])  # row1 占位
    ws.append(["订单编号", "名称", "刷单日期", "刷手", "买家秀是否自动生成"])  # row2 表头
    for order_id, name, status in rows:
        ws.append([order_id, name, "2026-05-01", "张三", status])
    wb.save(path)


def _records():
    return [{"row": 3, "order_id": "O1", "name": "商品A", "brusher": "张三", "order_date_key": "20260501"}]


def _summary():
    return {
        "date_column": "刷单日期",
        "skipped_generated_count": 0,
        "pending_date_keys": ["20260501"],
        "pending_records_by_date": {"20260501": 1},
        "selected_order_ids": ["O1"],
    }


def _batch_plan():
    return [{"date_key": "20260501", "records": _records(), "groups": [("g1", [Path("/x/a.jpg")])]}]


def _meta():
    return {"source_mode": "grouped", "rotation_cursor_before": 0, "rotation_cursor_after": 1}


def _patch_common(monkeypatch, tmp_path: Path, danger: dict):
    monkeypatch.setattr(packager,"read_matches", lambda wb, model: (_records(), "商品A", {"订单编号": 0, "名称": 1}, _summary()))
    monkeypatch.setattr(packager,"select_group_batches", lambda **k: (_batch_plan(), _meta()))
    monkeypatch.setattr(packager,"package_zip", lambda *a, **k: danger.__setitem__("zip", danger.get("zip", 0) + 1) or (tmp_path / "out.zip", []))
    monkeypatch.setattr(packager,"verify_zip", lambda *a, **k: {})
    monkeypatch.setattr(packager,"patch_workbook", lambda *a, **k: danger.__setitem__("patch", danger.get("patch", 0) + 1) or (None, []))
    monkeypatch.setattr(packager,"set_rotation_cursor", lambda **k: danger.__setitem__("rot_set", danger.get("rot_set", 0) + 1))
    monkeypatch.setattr(packager,"reset_rotation_cursor", lambda key: danger.__setitem__("rot_reset", danger.get("rot_reset", 0) + 1))
    monkeypatch.setattr(packager,"grouped_sources", lambda base, batch=None: [])


def test_workflow_registers() -> None:
    wf = discover_workflow("buyer_show")
    assert wf.id == "buyer_show"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "scan_buyer_show_sources",
        "select_groups",
        "build_zip_packages",
        "update_register",
        "collect_artifacts",
    ]


def test_dry_run_no_args_is_safe(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)
    assert run.status == "dry_run_success"


def test_dry_run_does_not_package_or_patch(monkeypatch, tmp_path: Path) -> None:
    danger: dict = {}
    _patch_common(monkeypatch, tmp_path, danger)
    workbook = tmp_path / "register.xlsx"
    workbook.write_bytes(b"PK")

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": True,
            "args": ["--dry-run", "--buyer-show-path", str(tmp_path), "--model", "AQA-12D-838", "--workbook", str(workbook), "--reset-rotation"],
        },
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert danger.get("zip", 0) == 0       # 未打包、未复制图片
    assert danger.get("patch", 0) == 0     # 未回写登记表
    assert danger.get("rot_set", 0) == 0   # 未推进轮询
    assert danger.get("rot_reset", 0) == 0  # dry-run 也不重置轮询

    select_step = json.loads((runner.last_run_dir / "steps" / "select_groups.json").read_text(encoding="utf-8"))
    assert select_step["outputs"]["can_execute"] is True


def test_real_run_packages_and_patches(monkeypatch, tmp_path: Path) -> None:
    danger: dict = {}
    _patch_common(monkeypatch, tmp_path, danger)
    workbook = tmp_path / "register.xlsx"
    workbook.write_bytes(b"PK")

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": False,
            "args": ["--buyer-show-path", str(tmp_path), "--model", "AQA-12D-838", "--workbook", str(workbook)],
        },
        dry_run=False,
    )

    assert run.status == "success"
    assert danger.get("zip", 0) == 1     # 打包一次
    assert danger.get("patch", 0) == 1   # 回写一次
    assert danger.get("rot_set", 0) == 1  # 推进轮询一次
    artifacts = json.loads((runner.last_run_dir / "artifacts.json").read_text(encoding="utf-8"))
    assert any(a["role"] == "buyer_show_package" for a in artifacts)
    assert not any(a["role"] == "register_backup" for a in artifacts)


# --- 默认型号自动推断（只给买家秀路径时） ---


def test_detect_default_model_picks_first_pending(tmp_path: Path) -> None:
    register = tmp_path / "register.xlsx"
    _make_register(
        register,
        [
            ("O1", "【奥克斯】按摩靠垫AQA-24D-838(红色)", "是"),   # 已生成，跳过
            ("O2", "【奥克斯】按摩靠垫AQA-24D-820(蓝色)", ""),     # 第一条待处理 → 期望
            ("O3", "其他型号XYZ-1", ""),
        ],
    )
    assert packager.detect_default_model(register) == "【奥克斯】按摩靠垫AQA-24D-820(蓝色)"


def test_detect_default_model_no_pending_raises(tmp_path: Path) -> None:
    register = tmp_path / "register.xlsx"
    _make_register(register, [("O1", "型号A", "是"), ("O2", "型号B", "是")])
    with pytest.raises(SystemExit):
        packager.detect_default_model(register)


def test_check_inputs_auto_detects_model_when_only_path(monkeypatch, tmp_path: Path) -> None:
    register = tmp_path / "register.xlsx"
    _make_register(
        register,
        [
            ("O1", "【奥克斯】按摩靠垫AQA-24D-838(红色)", "是"),
            ("O2", "【奥克斯】按摩靠垫AQA-24D-820(蓝色)", ""),
        ],
    )
    # 让分组等后续逻辑不依赖真实图片
    monkeypatch.setattr(packager,"read_matches", lambda wb, model: (_records(), "商品A", {"订单编号": 0, "名称": 1}, _summary()))

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": True,
            "args": ["--dry-run", "--buyer-show-path", str(tmp_path), "--workbook", str(register)],
        },
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    check = json.loads((runner.last_run_dir / "steps" / "check_inputs.json").read_text(encoding="utf-8"))
    assert check["outputs"]["model_auto_detected"] is True
    assert check["outputs"]["model"] == "【奥克斯】按摩靠垫AQA-24D-820(蓝色)"


def test_explicit_model_not_overridden(monkeypatch, tmp_path: Path) -> None:
    register = tmp_path / "register.xlsx"
    _make_register(register, [("O1", "【奥克斯】按摩靠垫AQA-24D-820(蓝色)", "")])
    monkeypatch.setattr(packager,"read_matches", lambda wb, model: (_records(), "商品A", {"订单编号": 0, "名称": 1}, _summary()))

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": True,
            "args": ["--dry-run", "--buyer-show-path", str(tmp_path), "--model", "AQA-12D-838", "--workbook", str(register)],
        },
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    check = json.loads((runner.last_run_dir / "steps" / "check_inputs.json").read_text(encoding="utf-8"))
    assert check["outputs"]["model_auto_detected"] is False
    assert check["outputs"]["model"] == "AQA-12D-838"
