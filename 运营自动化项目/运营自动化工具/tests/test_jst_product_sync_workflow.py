from __future__ import annotations

import json
import sys
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from tasks.jst_product_sync import main as task_entry
from workflows.jst_product_sync import steps
from workflows.jst_product_sync.workflow import build_workflow


def test_workflow_registers() -> None:
    wf = discover_workflow("jst_product_sync")
    assert wf.id == "jst_product_sync"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "check_local_source",
        "download_jst_products",
        "validate_products",
        "update_master_data",
        "collect_artifacts",
    ]


def test_main_routes_to_workflow_without_direct_ops_call(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["jst_product_sync", "--dry-run", "--use-local-only"])
    monkeypatch.setattr(task_entry, "_run_workflow", lambda args: calls.append(list(args)) or 0, raising=False)
    monkeypatch.setattr(
        task_entry,
        "run_ops_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("旧入口不应直接请求 Ops-Cli")),
    )

    assert task_entry.main() == 0
    assert calls == [["jst_product_sync", "--dry-run", "--use-local-only"]]


def test_dry_run_passes_dry_run_and_keep_brands(monkeypatch, tmp_path: Path) -> None:
    seen: list = []
    monkeypatch.setattr(steps, "run_ops_json", lambda command, *a, **k: seen.append(list(command)) or {"success": True, "data": {}})

    runner = WorkflowRunner(tmp_path)
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--keep-brands", "奥克斯", "苏泊尔"]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert "--dry-run" in seen[0]
    assert "--keep-brands" in seen[0] and "奥克斯" in seen[0] and "苏泊尔" in seen[0]
    update_step = json.loads((runner.last_run_dir / "steps" / "update_master_data.json").read_text(encoding="utf-8"))
    assert update_step["outputs"]["skipped"] is True


def test_dry_run_use_local_only(monkeypatch, tmp_path: Path) -> None:
    seen: list = []
    monkeypatch.setattr(steps, "run_ops_json", lambda command, *a, **k: seen.append(list(command)) or {"success": True, "data": {}})

    runner = WorkflowRunner(tmp_path)
    runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run", "--use-local-only"]}, dry_run=True)

    assert "--use-local-only" in seen[0]


def test_dry_run_tolerates_platform_error(monkeypatch, tmp_path: Path) -> None:
    def boom(command, *a, **k):
        raise RuntimeError("Ops-Cli 执行失败 [AUTH_REQUIRED]")

    monkeypatch.setattr(steps, "run_ops_json", boom)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)
    assert run.status == "dry_run_success"  # 平台未就绪时安全降级


def test_real_run_writes_master_and_artifact(monkeypatch, tmp_path: Path) -> None:
    latest = tmp_path / "jst_master.xlsx"
    latest.write_bytes(b"PK")
    seen: list = []

    def fake(command, *a, **k):
        seen.append(list(command))
        return {"success": True, "data": {"latest_file": str(latest), "targets": ["a", "b"]}}

    monkeypatch.setattr(steps, "run_ops_json", fake)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": False, "args": []}, dry_run=False)

    assert run.status == "success"
    assert "--dry-run" not in seen[0]
    artifacts = json.loads((runner.last_run_dir / "artifacts.json").read_text(encoding="utf-8"))
    assert any(a["role"] == "master_latest" and a["platform"] == "jst" for a in artifacts)
