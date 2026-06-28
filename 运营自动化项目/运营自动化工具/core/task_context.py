from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import get_path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class TaskContext:
    def __init__(self, task_name: str, *, task_id: str | None = None) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.data: dict[str, Any] = {
            "task_id": task_id or f"{task_name}_{stamp}",
            "task_name": task_name,
            "created_at": now_iso(),
            "status": "partial",
            "inputs": {},
            "outputs": {},
            "artifacts": [],
            "next_tasks": [],
            "errors": [],
        }

    def add_input(self, key: str, value: Any) -> None:
        self.data["inputs"][key] = _jsonable(value)

    def add_output(self, key: str, value: Any) -> None:
        self.data["outputs"][key] = _jsonable(value)

    def add_artifact(self, path: Path | str, *, kind: str = "file") -> None:
        self.data["artifacts"].append({"kind": kind, "path": str(path)})

    def add_next_task(self, task_name: str, payload: dict[str, Any] | None = None) -> None:
        self.data["next_tasks"].append({"task_name": task_name, "payload": _jsonable(payload or {})})

    def add_error(self, error: str, detail: Any | None = None) -> None:
        item = {"error": str(error)}
        if detail is not None:
            item["detail"] = _jsonable(detail)
        self.data["errors"].append(item)

    def finish(self, status: str) -> Path:
        self.data["status"] = status
        self.data["finished_at"] = now_iso()
        return self.write()

    def write(self) -> Path:
        context_dir = get_path("runtime_dir") / "context"
        context_dir.mkdir(parents=True, exist_ok=True)
        path = context_dir / f"{self.data['task_id']}.json"
        path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)
