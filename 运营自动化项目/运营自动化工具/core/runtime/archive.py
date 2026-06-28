"""workflow 运行与 Artifact 的全局归档索引。

每次 workflow 运行结束，WorkflowRunner 会把一条精简记录追加到 runs_root/index.jsonl，
便于跨 run 快速列出历史运行、检索产物，而无需逐个打开 run.json。

索引是 append-only 的 JSONL；reindex() 可从现有 run.json 重建（用于回填历史运行）。
索引落在 runtime/runs/ 内，已被 .gitignore 排除，不进入版本库。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.runtime.models import TaskRun

INDEX_NAME = "index.jsonl"


def _artifact_brief(artifact: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": artifact.get("type", ""),
        "role": artifact.get("role", ""),
        "name": artifact.get("name", ""),
        "path": artifact.get("path", ""),
        "platform": artifact.get("platform", ""),
        "month": artifact.get("month", ""),
    }


def _record_from_run(run_dict: dict[str, Any], run_dir: str) -> dict[str, Any]:
    return {
        "run_id": run_dict.get("run_id"),
        "workflow_id": run_dict.get("workflow_id"),
        "workflow_name": run_dict.get("workflow_name", ""),
        "status": run_dict.get("status"),
        "dry_run": bool(run_dict.get("dry_run")),
        "started_at": run_dict.get("started_at"),
        "finished_at": run_dict.get("finished_at"),
        "run_dir": run_dir,
        "step_count": len(run_dict.get("steps", []) or []),
        "error_count": len(run_dict.get("errors", []) or []),
        "artifacts": [_artifact_brief(a) for a in (run_dict.get("artifacts") or [])],
    }


class RunIndex:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = Path(runs_root)
        self.index_path = self.runs_root / INDEX_NAME

    def append(self, run: TaskRun, run_dir: Path) -> None:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        record = _record_from_run(run.to_dict(), str(run_dir))
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def list_runs(self, *, limit: int = 20, workflow_id: str | None = None) -> list[dict[str, Any]]:
        records = self._read()
        if workflow_id:
            records = [r for r in records if r.get("workflow_id") == workflow_id]
        records.sort(key=lambda r: str(r.get("started_at") or ""))
        return records[-limit:][::-1] if limit else records[::-1]

    def search_artifacts(
        self,
        query: str | None = None,
        *,
        role: str | None = None,
        platform: str | None = None,
        month: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        needle = (query or "").strip().lower()
        results: list[dict[str, Any]] = []
        for record in self._read():
            for artifact in record.get("artifacts") or []:
                if role and artifact.get("role") != role:
                    continue
                if platform and artifact.get("platform") != platform:
                    continue
                if month and artifact.get("month") != month:
                    continue
                if needle:
                    haystack = " ".join(
                        str(artifact.get(key) or "") for key in ("type", "role", "name", "path", "platform", "month")
                    ).lower()
                    if needle not in haystack:
                        continue
                results.append(
                    {
                        **artifact,
                        "run_id": record.get("run_id"),
                        "workflow_id": record.get("workflow_id"),
                        "finished_at": record.get("finished_at"),
                    }
                )
        return results[:limit] if limit else results

    def reindex(self) -> int:
        """从 runs_root 下现有 run.json 重建索引，返回收录的 run 数量。"""
        records: list[dict[str, Any]] = []
        for run_json in self.runs_root.glob("*/*/run.json"):
            try:
                data = json.loads(run_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            records.append(_record_from_run(data, str(run_json.parent)))
        records.sort(key=lambda r: str(r.get("started_at") or ""))
        self.runs_root.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return len(records)
