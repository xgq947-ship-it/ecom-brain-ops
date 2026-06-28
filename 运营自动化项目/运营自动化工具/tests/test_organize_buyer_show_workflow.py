from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.organize_buyer_show import steps
from workflows.organize_buyer_show.workflow import build_workflow


def _make_buyer(dir_path: Path, n: int) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (dir_path / f"img{i}.jpg").write_bytes(b"x")


def _make_tree(base: Path) -> None:
    """SKU 层级下的若干买家秀：部分低质(≤3)、部分保留(>3)。"""
    _make_buyer(base / "SKU-A" / "买家秀1", 5)   # 保留
    _make_buyer(base / "SKU-A" / "买家秀2", 2)   # 删
    _make_buyer(base / "SKU-B" / "数据包" / "买家秀3", 4)  # 保留（嵌套）
    _make_buyer(base / "SKU-B" / "数据包" / "买家秀4", 1)  # 删


def test_workflow_registers() -> None:
    wf = discover_workflow("organize_buyer_show")
    assert wf.id == "organize_buyer_show"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "scan_preview",
        "delete_low_quality",
        "flatten_sku",
        "verify_collect",
    ]


def test_find_buyer_shows_recursive(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    found = {Path(p).name: c for _, c, p in steps.find_buyer_shows(tmp_path)}
    assert found == {"买家秀1": 5, "买家秀2": 2, "买家秀3": 4, "买家秀4": 1}


def test_dry_run_no_args_is_safe(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)
    assert run.status == "dry_run_success"


def test_dry_run_does_not_delete_or_move(tmp_path: Path) -> None:
    target = tmp_path / "买家秀"
    _make_tree(target)
    before = sorted(p.name for p in target.iterdir())

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--path", str(target)]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    # 目录结构零改动：低质未删、未平铺
    assert sorted(p.name for p in target.iterdir()) == before
    assert (target / "SKU-A" / "买家秀2").exists()  # 低质仍在
    # 扫描预览正确分类
    scan = json.loads((runner.last_run_dir / "steps" / "scan_preview.json").read_text(encoding="utf-8"))
    assert scan["outputs"]["to_delete_count"] == 2
    assert scan["outputs"]["to_keep_count"] == 2


def test_no_execute_is_preview_only(tmp_path: Path) -> None:
    target = tmp_path / "买家秀"
    _make_tree(target)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--path", str(target)]},  # 无 --execute
        dry_run=False,
    )

    assert run.status == "success"
    assert (target / "SKU-A" / "买家秀2").exists()  # 未带 --execute 不删除
    assert (target / "SKU-A").is_dir()              # 未平铺


def test_execute_deletes_and_flattens(tmp_path: Path) -> None:
    target = tmp_path / "买家秀"
    _make_tree(target)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--path", str(target), "--execute"]},
        dry_run=False,
    )

    assert run.status == "success"
    # 低质已删、保留的已平铺到根目录、SKU 层级清空
    remaining = sorted(p.name for p in target.iterdir() if p.is_dir())
    assert remaining == ["买家秀1", "买家秀3"]
    assert not (target / "SKU-A").exists()
    assert not (target / "SKU-B").exists()

    final = json.loads((runner.last_run_dir / "steps" / "verify_collect.json").read_text(encoding="utf-8"))
    assert final["outputs"]["final_buyer_count"] == 2
    assert final["outputs"]["final_image_count"] == 9  # 5 + 4


def test_no_flatten_keeps_hierarchy(tmp_path: Path) -> None:
    target = tmp_path / "买家秀"
    _make_tree(target)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--path", str(target), "--execute", "--no-flatten"]},
        dry_run=False,
    )

    assert run.status == "success"
    assert not (target / "SKU-A" / "买家秀2").exists()  # 低质删除照常
    assert (target / "SKU-A" / "买家秀1").exists()       # 但层级保留
    assert (target / "SKU-A").is_dir()


def test_name_conflict_aborts(tmp_path: Path) -> None:
    target = tmp_path / "买家秀"
    _make_buyer(target / "SKU-A" / "买家秀同名", 5)
    _make_buyer(target / "SKU-B" / "买家秀同名", 5)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--path", str(target), "--execute"]},
        dry_run=False,
    )

    assert run.status == "failed"
    # 中止：未移动任何文件，两个同名买家秀都还在原处
    assert (target / "SKU-A" / "买家秀同名").exists()
    assert (target / "SKU-B" / "买家秀同名").exists()
