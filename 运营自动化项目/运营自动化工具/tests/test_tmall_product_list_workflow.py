from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.tmall_product_list import steps
from workflows.tmall_product_list.workflow import build_workflow


def test_workflow_registers() -> None:
    wf = discover_workflow("tmall_product_list")
    assert wf.id == "tmall_product_list"
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "check_local_source",
        "download_tmcs_products",
        "validate_products",
        "update_master_data",
        "collect_artifacts",
    ]


def test_dry_run_passes_dry_run_to_ops(monkeypatch, tmp_path: Path) -> None:
    seen: list = []

    def fake(command, *a, **k):
        seen.append(list(command))
        return {"success": True, "command": "product sync", "data": {"sync_summary": {"added": 0}, "latest_file": None}}

    monkeypatch.setattr(steps, "run_ops_json", fake)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)

    assert run.status == "dry_run_success"
    assert "--dry-run" in seen[0]
    update_step = json.loads((runner.last_run_dir / "steps" / "update_master_data.json").read_text(encoding="utf-8"))
    assert update_step["outputs"]["skipped"] is True


def test_dry_run_with_skip_auto_download(monkeypatch, tmp_path: Path) -> None:
    seen: list = []
    monkeypatch.setattr(steps, "run_ops_json", lambda command, *a, **k: seen.append(list(command)) or {"success": True, "data": {}})

    runner = WorkflowRunner(tmp_path)
    runner.run(build_workflow(), inputs={"dry_run": True, "args": ["--dry-run", "--skip-auto-download"]}, dry_run=True)

    assert "--dry-run" in seen[0]
    assert "--use-local-only" in seen[0]


def test_real_run_writes_master_and_artifact(monkeypatch, tmp_path: Path) -> None:
    latest = tmp_path / "master.xlsx"
    latest.write_bytes(b"PK")
    seen: list = []

    def fake(command, *a, **k):
        seen.append(list(command))
        return {"success": True, "data": {"latest_file": str(latest), "sync_summary": {"added": 3}}}

    monkeypatch.setattr(steps, "run_ops_json", fake)

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": False, "args": []}, dry_run=False)

    assert run.status == "success"
    assert "--dry-run" not in seen[0]
    artifacts = json.loads((runner.last_run_dir / "artifacts.json").read_text(encoding="utf-8"))
    assert any(a["role"] == "master_latest" for a in artifacts)


def test_real_run_reads_ops_cli_output_path_and_flat_summary(monkeypatch, tmp_path: Path) -> None:
    latest = tmp_path / "master.xlsx"
    latest.write_bytes(b"PK")

    monkeypatch.setattr(
        steps,
        "run_ops_json",
        lambda command, *a, **k: {
            "success": True,
            "data": {
                "source": str(tmp_path / "import.xlsx"),
                "output_path": str(latest),
                "new_rows": 12,
                "final_latest_rows": 688,
            },
        },
    )

    runner = WorkflowRunner(tmp_path)
    run = runner.run(build_workflow(), inputs={"dry_run": False, "args": []}, dry_run=False)

    assert run.status == "success"
    update_step = json.loads((runner.last_run_dir / "steps" / "update_master_data.json").read_text(encoding="utf-8"))
    validate_step = json.loads((runner.last_run_dir / "steps" / "validate_products.json").read_text(encoding="utf-8"))
    assert update_step["outputs"]["latest_file"] == str(latest)
    assert validate_step["outputs"]["sync_summary"] == {"new_rows": 12, "final_latest_rows": 688}
