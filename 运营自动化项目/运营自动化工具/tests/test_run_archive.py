from __future__ import annotations

import json
from pathlib import Path

from core.runtime import Artifact, RunIndex, WorkflowRunner, build_workflow, step, success_result


def _wf(artifact: Artifact | None = None):
    def handler(ctx):
        return success_result(outputs={"ok": True}, artifacts=[artifact] if artifact else [])

    return build_workflow("demo", "Demo", [step("a", "a", handler)])


def test_runner_appends_to_index(tmp_path: Path) -> None:
    art = Artifact(type="xlsx", role="output", name="r.xlsx", path="/tmp/r.xlsx", platform="tmcs", month="2026-05")
    runner = WorkflowRunner(tmp_path)
    runner.run(_wf(art), inputs={"k": "v"})

    index_path = tmp_path / "index.jsonl"
    assert index_path.exists()
    records = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["workflow_id"] == "demo"
    assert records[0]["artifacts"][0]["name"] == "r.xlsx"


def test_list_runs_most_recent_first(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    runner.run(_wf(), inputs={})
    runner.run(_wf(), inputs={})

    runs = RunIndex(tmp_path).list_runs(limit=10)
    assert len(runs) == 2
    assert runs[0]["started_at"] >= runs[1]["started_at"]


def test_search_artifacts_by_filters(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    runner.run(_wf(Artifact(type="xlsx", role="output", name="alpha.xlsx", path="/a", platform="tmcs", month="2026-05")), inputs={})
    runner.run(_wf(Artifact(type="csv", role="promotion_source", name="beta.csv", path="/b", platform="jst", month="2026-04")), inputs={})

    index = RunIndex(tmp_path)
    assert len(index.search_artifacts(role="output")) == 1
    assert index.search_artifacts(platform="jst")[0]["name"] == "beta.csv"
    assert index.search_artifacts("alpha")[0]["name"] == "alpha.xlsx"
    assert index.search_artifacts(month="2026-04")[0]["type"] == "csv"


def test_reindex_rebuilds_from_run_json(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path)
    runner.run(_wf(), inputs={})
    runner.run(_wf(), inputs={})

    index = RunIndex(tmp_path)
    index.index_path.unlink()
    assert not index.index_path.exists()
    count = index.reindex()
    assert count == 2
    assert len(index.list_runs(limit=10)) == 2
