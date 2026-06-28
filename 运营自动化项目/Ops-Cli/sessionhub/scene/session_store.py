from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SESSION_ROOT = ROOT / "data" / "sessions"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class SessionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or SESSION_ROOT

    def path_for(self, site: str, scene: str) -> Path:
        return self.root / site / f"{scene}.json"

    def load(self, site: str, scene: str) -> dict[str, Any] | None:
        path = self.path_for(site, scene)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, site: str, scene: str, data: dict[str, Any]) -> Path:
        path = self.path_for(site, scene)
        path.parent.mkdir(parents=True, exist_ok=True)
        old = self.load(site, scene) or {}
        created_at = old.get("created_at") or data.get("created_at") or now_iso()
        payload = {**old, **data, "created_at": created_at, "updated_at": now_iso()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def update(self, site: str, scene: str, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.load(site, scene)
        if current is None:
            raise FileNotFoundError(f"session 不存在：{self.path_for(site, scene)}")
        current.update(updates)
        self.save(site, scene, current)
        return current

    def list_sessions(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.root.exists():
            return rows
        for path in sorted(self.root.glob("*/*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            rows.append(
                {
                    "site": data.get("site") or path.parent.name,
                    "scene": data.get("scene") or path.stem,
                    "status": data.get("status", "unknown"),
                    "updated_at": data.get("updated_at", ""),
                    "last_check": data.get("last_check", ""),
                }
            )
        return rows
