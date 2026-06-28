"""workflow 发现：扫描 workflows/<id>/workflow.py 并调用其 build_workflow()。

与 core/task_registry.py 对称：task_registry 负责旧脚本任务发现，这里负责新 workflow 发现。
每个 workflow 包必须导出 build_workflow() -> Workflow。
"""

from __future__ import annotations

import importlib
from pathlib import Path

from core.runtime.models import Workflow

ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS_DIR = ROOT / "workflows"


def available_workflows() -> list[str]:
    if not _WORKFLOWS_DIR.is_dir():
        return []
    ids = []
    for child in sorted(_WORKFLOWS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("_") or child.name.startswith("."):
            continue
        if (child / "workflow.py").exists():
            ids.append(child.name)
    return ids


def discover_workflow(workflow_id: str) -> Workflow:
    package_dir = _WORKFLOWS_DIR / workflow_id
    if not (package_dir / "workflow.py").exists():
        valid = "、".join(available_workflows()) or "（无）"
        raise SystemExit(f"未知 workflow：{workflow_id}\n可用 workflow：{valid}")
    module = importlib.import_module(f"workflows.{workflow_id}.workflow")
    builder = getattr(module, "build_workflow", None)
    if builder is None:
        raise SystemExit(f"workflow {workflow_id} 缺少 build_workflow() 入口")
    workflow = builder()
    if not isinstance(workflow, Workflow):
        raise SystemExit(f"workflow {workflow_id} 的 build_workflow() 未返回 Workflow")
    return workflow
