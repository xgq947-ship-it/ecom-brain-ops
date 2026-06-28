"""workflow 运行记录落盘。

目录布局：
  runtime/runs/YYYY-MM/run_xxx/
    run.json
    steps/<step_id>.json
    artifacts.json
"""

from __future__ import annotations

import json
from pathlib import Path

from core.runtime.models import StepRun, TaskRun


class RunStorage:
    def __init__(self, run: TaskRun, runs_root: Path) -> None:
        self.run = run
        month = (run.started_at or "")[:7] or "0000-00"
        self.run_dir = runs_root / month / run.run_id
        self.steps_dir = self.run_dir / "steps"

    def ensure_dirs(self) -> None:
        self.steps_dir.mkdir(parents=True, exist_ok=True)

    def write_step(self, step: StepRun) -> Path:
        self.ensure_dirs()
        path = self.steps_dir / f"{step.step_id}.json"
        _dump(path, step.to_dict())
        return path

    def write_artifacts(self) -> Path:
        self.ensure_dirs()
        path = self.run_dir / "artifacts.json"
        _dump(path, [artifact.to_dict() for artifact in self.run.artifacts])
        return path

    def write_run(self) -> Path:
        self.ensure_dirs()
        path = self.run_dir / "run.json"
        _dump(path, self.run.to_dict())
        return path


def _dump(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
