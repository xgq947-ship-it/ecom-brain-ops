from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_loader import get_path
from .task_registry import task_scripts


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _retry_dir() -> Path:
    path = get_path("runtime_dir") / "retry"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _project_root() -> Path:
    return get_path("project_root")


def add_retry(task_name: str, payload: dict[str, Any], reason: str) -> Path:
    retry_id = f"{task_name}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    data = {
        "retry_id": retry_id,
        "task_name": task_name,
        "status": "pending",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "reason": reason,
        "payload": payload,
    }
    path = _retry_dir() / f"{retry_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def list_retries() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(_retry_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("status") != "done":
            rows.append({**data, "path": str(path)})
    return rows


def replay_retry(retry_id: str, *, execute: bool = False) -> dict[str, Any]:
    path = _retry_dir() / f"{retry_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"找不到 retry：{retry_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    command = _build_replay_command(data, execute=execute)
    started_at = _now_iso()
    result = subprocess.run(command, capture_output=True, text=True)
    finished_at = _now_iso()
    replay = {
        "retry_id": retry_id,
        "task_name": data.get("task_name"),
        "execute": execute,
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    data.setdefault("replays", []).append(replay)
    data["updated_at"] = _now_iso()
    if execute and result.returncode == 0:
        data["status"] = "done"
        data["done_at"] = _now_iso()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return replay


def replay_all(*, execute: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in list_retries():
        results.append(replay_retry(str(item["retry_id"]), execute=execute))
    return results


def mark_done(retry_id: str) -> Path:
    path = _retry_dir() / f"{retry_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"找不到 retry：{retry_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "done"
    data["updated_at"] = _now_iso()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _build_replay_command(data: dict[str, Any], *, execute: bool) -> list[str]:
    task_name = str(data.get("task_name") or "")
    if task_name not in task_scripts():
        raise RuntimeError(f"retry 里的任务不存在或未注册：{task_name}")
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}

    args = [str(item) for item in payload.get("args", [])] if isinstance(payload.get("args"), list) else []
    if task_name == "tag_jst_brush_orders" and payload.get("order_no"):
        input_path = _retry_dir() / f"{data['retry_id']}_input.json"
        input_payload = {
            "date": datetime.now().date().isoformat(),
            "orders": [str(payload["order_no"])],
            "source_retry_id": data["retry_id"],
        }
        input_path.write_text(json.dumps(input_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args = ["--input", str(input_path)]

    if not execute and "--dry-run" not in args:
        args.append("--dry-run")
    if execute:
        args = [arg for arg in args if arg != "--dry-run"]

    return [sys.executable, str(_project_root() / "run.py"), task_name, *args]
