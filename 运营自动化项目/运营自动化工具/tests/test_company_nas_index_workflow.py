from __future__ import annotations

import json
import sys
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
import tasks.company_nas_index as task_entry

from workflows.company_nas_index import steps
from workflows.company_nas_index.workflow import build_workflow


def _patch_nas(monkeypatch, calls: dict, records=None):
    monkeypatch.setattr(steps.nas, "active_nas_mount", lambda: None)
    monkeypatch.setattr(steps.nas, "mount_nas", lambda: calls.__setitem__("mount", calls.get("mount", 0) + 1))
    monkeypatch.setattr(steps.nas, "unmount_nas", lambda: calls.__setitem__("unmount", calls.get("unmount", 0) + 1))
    monkeypatch.setattr(steps.nas, "nas_product_root", lambda: Path("/nas/products"))
    monkeypatch.setattr(steps.indexer, "scan_index", lambda root, **k: records if records is not None else [{"type": "dir", "brand": "奥克斯"}])
    monkeypatch.setattr(steps.indexer, "summarize", lambda recs: {"dir_count": len(recs), "file_count": 0, "brand_count": 1, "category_count": 0, "heavy_file_count": 0})
    monkeypatch.setattr(steps.indexer, "write_json", lambda *a, **k: calls.__setitem__("json", calls.get("json", 0) + 1))
    monkeypatch.setattr(steps.indexer, "write_csv", lambda *a, **k: calls.__setitem__("csv", calls.get("csv", 0) + 1))
    monkeypatch.setattr(steps.indexer, "write_md", lambda *a, **k: calls.__setitem__("md", calls.get("md", 0) + 1))


def test_workflow_registers() -> None:
    wf = discover_workflow("company_nas_index")
    assert wf.id == "company_nas_index"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "scan_nas",
        "build_index",
        "save_index",
        "collect_artifacts",
    ]


def test_dry_run_scans_but_does_not_write_index(monkeypatch, tmp_path: Path) -> None:
    calls: dict = {}
    _patch_nas(monkeypatch, calls)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)

    assert run.status == "dry_run_success"
    assert calls.get("json", 0) == 0  # 未写正式索引
    assert calls.get("csv", 0) == 0
    assert calls.get("md", 0) == 0
    assert calls.get("mount", 0) == 1
    assert calls.get("unmount", 0) == 1  # 收尾卸载
    save_step = json.loads((runner.last_run_dir / "steps" / "save_index.json").read_text(encoding="utf-8"))
    assert save_step["outputs"]["skipped"] is True


def test_real_run_writes_index(monkeypatch, tmp_path: Path) -> None:
    calls: dict = {}
    _patch_nas(monkeypatch, calls)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": False, "args": []}, dry_run=False)

    assert run.status == "success"
    assert calls.get("json", 0) == 1
    assert calls.get("csv", 0) == 1
    assert calls.get("md", 0) == 1


def test_search_mode_is_readonly(monkeypatch, tmp_path: Path) -> None:
    calls: dict = {}
    _patch_nas(monkeypatch, calls)
    monkeypatch.setattr(steps.indexer, "search_index", lambda q, limit: {"query": q, "match_count": 2, "matches": [{"path": "/a"}, {"path": "/b"}]})

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["奥克斯", "--dry-run"]}, dry_run=True)

    assert run.status == "dry_run_success"
    assert calls.get("mount", 0) == 0  # 搜索不挂载
    assert calls.get("json", 0) == 0
    collect = json.loads((runner.last_run_dir / "steps" / "collect_artifacts.json").read_text(encoding="utf-8"))
    assert collect["outputs"]["match_count"] == 2


def test_legacy_main_routes_to_workflow_without_scanning_or_writing(monkeypatch) -> None:
    # 薄 wrapper 只透传参数给 workflow：结构上不 import 扫描/写索引能力，故无从直连。
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["company_nas_index", "--dry-run"])
    monkeypatch.setattr(task_entry, "_run_workflow", lambda args: calls.append(list(args)) or 0, raising=False)

    assert task_entry.main() == 0
    assert calls == [["company_nas_index", "--dry-run"]]
    assert not hasattr(task_entry, "scan_index")
    assert not hasattr(task_entry, "write_json")
