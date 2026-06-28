import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ops_cli.config import get_config


def write_runtime_context(
    *,
    task_name: str,
    status: str,
    inputs: dict[str, Any],
    outputs: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    artifacts: list[str] | None = None,
) -> Path:
    runtime_dir = Path(get_config().runtime_dir)
    context_dir = runtime_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = context_dir / f"{task_name}_{timestamp}.json"
    payload = {
        "task_name": task_name,
        "status": status,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": inputs,
        "outputs": outputs or {},
        "errors": errors or [],
        "artifacts": artifacts or [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
