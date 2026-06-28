from __future__ import annotations

import json
import importlib.util
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from core.task_registry import resolve_task

from workflows.ai_knowledge_base_update import steps
from workflows.ai_knowledge_base_update.document_sync import (
    DocumentPlan,
    apply_document_update,
)
from workflows.ai_knowledge_base_update.workflow import build_workflow


def test_tmcs_fund_table_generate_is_hermes_auto_safe() -> None:
    source_root = Path(__file__).resolve().parents[2]
    module_path = source_root / "scripts" / "sync_ai_knowledge_base.py"
    spec = importlib.util.spec_from_file_location("sync_ai_knowledge_base", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "tmcs_fund_table_generate" in module.HERMES_AUTO_SAFE


def test_revenue_query_is_hermes_auto_safe() -> None:
    source_root = Path(__file__).resolve().parents[2]
    module_path = source_root / "scripts" / "sync_ai_knowledge_base.py"
    spec = importlib.util.spec_from_file_location("sync_ai_knowledge_base", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.PLATFORM_MAP["revenue_query"] == "jst"
    assert "revenue_query" in module.HERMES_AUTO_SAFE


def test_workflow_registers() -> None:
    wf = discover_workflow("ai_knowledge_base_update")
    assert wf.id == "ai_knowledge_base_update"
    assert [step.id for step in wf.steps] == [
        "check_inputs",
        "collect_context",
        "build_update_bundle",
        "apply_updates",
        "regenerate_dispatch",
        "validate_knowledge_base",
        "collect_outputs",
    ]


def test_chinese_alias_resolves() -> None:
    assert resolve_task("AI知识库同步更新") == "ai_knowledge_base_update"
    assert resolve_task("知识库同步更新") == "ai_knowledge_base_update"
    assert resolve_task("更新AI Knowledge Base") == "ai_knowledge_base_update"


def test_apply_update_preserves_manual_content(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(
        """---
type: overview
status: active
platform: all
updated: 2026-06-01
source: ai-updated
---

# 示例文档

<!-- AUTO-GENERATED:START -->

旧内容

<!-- AUTO-GENERATED:END -->

人工补充
""",
        encoding="utf-8",
    )

    plan = DocumentPlan(
        path=path,
        doc_type="overview",
        platform="all",
        title="示例文档",
        source_files=[],
    )
    result = apply_document_update(plan, "新内容", date_str="2026-06-03", dry_run=False)
    updated = path.read_text(encoding="utf-8")

    assert result["changed"] is True
    assert result["backup_path"] is None
    assert "新内容" in updated
    assert "人工补充" in updated
    assert "source: ai-updated" in updated
    assert "updated: 2026-06-03" in updated


def test_apply_update_preserves_workflow_dispatch_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "demo.md"
    path.write_text(
        """---
type: workflow
status: active
updated: 2026-06-01
source: ai-updated
workflow_id: demo
cn_name: "演示"
platform: "jst"
hermes_auto: true
dry_run_cmd: "python3 run.py workflow demo --dry-run"
run_cmd: "python3 run.py workflow demo"
triggers: ["demo", "演示"]
---

# 演示

<!-- AUTO-GENERATED:START -->
旧内容
<!-- AUTO-GENERATED:END -->
""",
        encoding="utf-8",
    )
    plan = DocumentPlan(path=path, doc_type="workflow", platform="local", title="演示", source_files=[])

    apply_document_update(plan, "新内容", date_str="2026-06-03", dry_run=False)
    updated = path.read_text(encoding="utf-8")

    assert 'cn_name: "演示"' in updated
    assert "platform: jst" in updated
    assert "hermes_auto: true" in updated
    assert 'triggers: ["demo", "演示"]' in updated
    assert "updated: 2026-06-03" in updated


def test_apply_update_accepts_frontmatter_for_new_workflow_doc(tmp_path: Path) -> None:
    path = tmp_path / "revenue_query.md"
    plan = DocumentPlan(path=path, doc_type="workflow", platform="local", title="营业额查询", source_files=[])

    apply_document_update(
        plan,
        "## 状态\n\nactive\n",
        date_str="2026-06-18",
        frontmatter={
            "cn_name": "营业额查询",
            "platform": "jst",
            "hermes_auto": True,
            "dry_run_cmd": "python3 run.py workflow revenue_query --dry-run",
            "run_cmd": "python3 run.py workflow revenue_query",
            "triggers": ["revenue_query", "营业额查询"],
        },
        dry_run=False,
    )
    updated = path.read_text(encoding="utf-8")

    assert 'cn_name: "营业额查询"' in updated
    assert "platform: jst" in updated
    assert "workflow_id: revenue_query" in updated
    assert "hermes_auto: true" in updated
    assert 'triggers: ["revenue_query", "营业额查询"]' in updated


def test_apply_update_creates_backup_when_markers_missing(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text("# 旧文档\n\n人工内容\n", encoding="utf-8")

    plan = DocumentPlan(
        path=path,
        doc_type="sop",
        platform="all",
        title="旧文档",
        source_files=[],
    )
    result = apply_document_update(plan, "自动内容", date_str="2026-06-03", dry_run=False)
    updated = path.read_text(encoding="utf-8")

    assert result["changed"] is True
    assert result["backup_path"]
    assert Path(result["backup_path"]).exists()
    assert "<!-- AUTO-GENERATED:START -->" in updated
    assert "人工内容" in updated


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _prepare_fixture(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    kb_root = tmp_path / "kb"

    _write(source_root / "README.md", "# 项目总览\n\n这里是 README。\n")
    _write(
        source_root / "运营自动化工具/workflows/demo_a/workflow.py",
        '''"""demo_a workflow。"""\n\nfrom __future__ import annotations\n\nfrom core.runtime import Workflow, build_workflow as _make_workflow, step\n\n\ndef noop(ctx):\n    from core.runtime import success_result\n    return success_result()\n\n\ndef build_workflow() -> Workflow:\n    return _make_workflow("demo_a", "演示A", [step("noop", "空步骤", noop)])\n''',
    )
    _write(
        source_root / "运营自动化工具/workflows/demo_a/README.md",
        "# demo_a\n\n## 入口\n\n```bash\npython3 run.py workflow demo_a --dry-run\n```\n",
    )
    _write(
        source_root / "运营自动化工具/tasks/demo_a.yaml",
        """name: demo_a\ntype: workflow\nworkflow: demo_a\ndescription: 演示A\naliases:\n  - 演示A\nentrypoint: demo_a.py\n""",
    )

    _write(
        kb_root / "00-总览/系统能力地图.md",
        """---
type: overview
status: active
platform: all
updated: 2026-06-01
source: ai-updated
---

# 系统能力地图

<!-- AUTO-GENERATED:START -->
旧总览
<!-- AUTO-GENERATED:END -->

人工备注
""",
    )
    _write(
        kb_root / "03-SOP/Codex开发交付SOP.md",
        """---
type: sop
status: active
platform: all
updated: 2026-06-01
source: ai-updated
---

# Codex开发交付SOP

<!-- AUTO-GENERATED:START -->
旧SOP
<!-- AUTO-GENERATED:END -->
""",
    )
    _write(
        kb_root / "01-工作流/orphan_old.md",
        """---
type: workflow
status: active
platform: local
updated: 2026-06-01
source: ai-updated
---

# orphan_old

<!-- AUTO-GENERATED:START -->
旧孤儿文档
<!-- AUTO-GENERATED:END -->
""",
    )
    return source_root, kb_root


def _patch_dispatch_generator(monkeypatch) -> None:
    monkeypatch.setattr(
        steps,
        "run_dispatch_generator",
        lambda source_root, kb_root: {
            "success": True,
            "returncode": 0,
            "stdout": "dispatch ok",
            "stderr": "",
            "command": ["gen_workflow_dispatch.py", "--kb-root", str(kb_root)],
        },
    )


def _run_workflow(
    monkeypatch,
    tmp_path: Path,
    args: list[str],
    *,
    dry_run: bool,
):
    source_root, kb_root = _prepare_fixture(tmp_path)
    seen: list[tuple[Path, Path]] = []

    monkeypatch.setattr(
        steps,
        "run_validation",
        lambda validate_script, cwd: seen.append((validate_script, cwd)) or {
            "success": True,
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "command": [str(validate_script)],
        },
    )
    _patch_dispatch_generator(monkeypatch)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": dry_run,
            "args": [
                "--source-root",
                str(source_root),
                "--kb-root",
                str(kb_root),
                *args,
            ],
        },
        dry_run=dry_run,
    )
    return run, runner, seen, source_root, kb_root


def test_bundle_contains_fixed_prompt_and_targets(monkeypatch, tmp_path: Path) -> None:
    run, runner, _, _, _ = _run_workflow(monkeypatch, tmp_path, args=[], dry_run=False)

    assert run.status == "success"
    outputs = json.loads((runner.last_run_dir / "steps" / "build_update_bundle.json").read_text(encoding="utf-8"))["outputs"]
    bundle_path = Path(outputs["bundle_path"])
    bundle_text = bundle_path.read_text(encoding="utf-8")

    assert "读取固定提示词" in bundle_text
    assert "00-总览/知识库版本.md" in bundle_text
    assert "00-总览/系统能力地图.md" in bundle_text
    assert "01-工作流/demo_a.md" in bundle_text
    assert "03-SOP/Hermes调用项目知识库SOP.md" in bundle_text
    assert "orphan_old" in bundle_text
    assert "建议归档" in bundle_text


def test_dry_run_with_updates_file_does_not_write(monkeypatch, tmp_path: Path) -> None:
    source_root, kb_root = _prepare_fixture(tmp_path)
    updates_file = tmp_path / "updates.json"
    updates_file.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "path": "01-工作流/demo_a.md",
                        "auto_generated_markdown": "## 自动内容\n\n这里是预览。\n",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    before_exists = (kb_root / "01-工作流/demo_a.md").exists()
    monkeypatch.setattr(
        steps,
        "run_validation",
        lambda validate_script, cwd: {
            "success": True,
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "command": [str(validate_script)],
        },
    )
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": True,
            "args": [
                "--source-root",
                str(source_root),
                "--kb-root",
                str(kb_root),
                "--updates-file",
                str(updates_file),
            ],
        },
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert (kb_root / "01-工作流/demo_a.md").exists() is before_exists


def test_real_run_applies_updates_and_validates(monkeypatch, tmp_path: Path) -> None:
    source_root, kb_root = _prepare_fixture(tmp_path)
    updates_file = tmp_path / "updates.json"
    updates_file.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "path": "01-工作流/demo_a.md",
                        "auto_generated_markdown": "## 自动内容\n\n来自当前 AI 会话。\n",
                    },
                    {
                        "path": "00-总览/系统能力地图.md",
                        "auto_generated_markdown": "## 自动更新后的系统能力地图\n",
                    },
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    seen: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        steps,
        "run_validation",
        lambda validate_script, cwd: seen.append((validate_script, cwd)) or {
            "success": True,
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "command": [str(validate_script)],
        },
    )
    _patch_dispatch_generator(monkeypatch)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": False,
            "args": [
                "--source-root",
                str(source_root),
                "--kb-root",
                str(kb_root),
                "--updates-file",
                str(updates_file),
            ],
        },
        dry_run=False,
    )

    assert run.status == "success"
    workflow_doc = kb_root / "01-工作流/demo_a.md"
    assert workflow_doc.exists()
    assert "来自当前 AI 会话" in workflow_doc.read_text(encoding="utf-8")
    assert seen

    outputs = json.loads((runner.last_run_dir / "steps" / "collect_outputs.json").read_text(encoding="utf-8"))["outputs"]
    assert outputs["changed_count"] == 2
    assert outputs["validation"]["success"] is True
    version_doc = kb_root / "00-总览/知识库版本.md"
    assert version_doc.exists()
    version_text = version_doc.read_text(encoding="utf-8")
    assert "## 当前版本号" in version_text
    assert "## 当前 workflow 数量" in version_text
    assert "validate 状态" in version_text
    assert "Hermes memory 与 AI Knowledge Base 内容冲突" in version_text


def test_archive_action_moves_orphan_doc(monkeypatch, tmp_path: Path) -> None:
    source_root, kb_root = _prepare_fixture(tmp_path)
    updates_file = tmp_path / "updates.json"
    updates_file.write_text(
        json.dumps(
            {
                "documents": [
                    {
                        "path": "01-工作流/orphan_old.md",
                        "action": "archive",
                        "auto_generated_markdown": "## 已归档\n\n源项目已不存在。\n",
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        steps,
        "run_validation",
        lambda validate_script, cwd: {
            "success": True,
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "command": [str(validate_script)],
        },
    )
    _patch_dispatch_generator(monkeypatch)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": False,
            "args": [
                "--source-root",
                str(source_root),
                "--kb-root",
                str(kb_root),
                "--updates-file",
                str(updates_file),
            ],
        },
        dry_run=False,
    )

    assert run.status == "success"
    assert not (kb_root / "01-工作流/orphan_old.md").exists()
    archived = kb_root / "99-归档" / "01-工作流" / "orphan_old.md"
    assert archived.exists()
    assert "source: ai-updated" in archived.read_text(encoding="utf-8")
