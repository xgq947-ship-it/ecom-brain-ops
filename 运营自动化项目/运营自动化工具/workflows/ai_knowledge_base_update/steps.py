from __future__ import annotations

import argparse
from datetime import datetime
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.ai_knowledge_base_update.document_sync import (
    DocumentPlan,
    apply_document_update,
    archive_document,
)


PROMPT_TEMPLATE = Path(__file__).resolve().parent / "prompt_template.md"
# 注：Workflow命令映射表.md 不在此列 —— 它由 gen_workflow_dispatch.py 从各 workflow
# 文件 frontmatter 确定性生成（ADR-003），AI 不再手写其内容，只维护 workflow frontmatter。
DEFAULT_OVERVIEW_DOCS = [
    ("00-总览/知识库版本.md", "overview", "all", "知识库版本"),
    ("00-总览/系统能力地图.md", "overview", "all", "系统能力地图"),
    ("00-总览/自动化工作流总览.md", "overview", "all", "自动化工作流总览"),
    ("00-总览/当前项目状态.md", "overview", "all", "当前项目状态"),
    ("00-总览/Hermes读取入口.md", "overview", "all", "Hermes读取入口"),
]
DEFAULT_PLATFORM_DOCS = [
    ("02-平台能力/tmcs猫超.md", "platform", "tmcs", "tmcs猫超"),
    ("02-平台能力/jst聚水潭.md", "platform", "jst", "jst聚水潭"),
    ("02-平台能力/本地Excel与主数据.md", "platform", "local", "本地Excel与主数据"),
    ("02-平台能力/浏览器与SessionHub.md", "platform", "multi", "浏览器与SessionHub"),
]
DEFAULT_SOP_DOCS = [
    ("03-SOP/新增自动化工作流SOP.md", "sop", "all", "新增自动化工作流SOP"),
    ("03-SOP/新增平台能力SOP.md", "sop", "all", "新增平台能力SOP"),
    ("03-SOP/Codex开发交付SOP.md", "sop", "all", "Codex开发交付SOP"),
    ("03-SOP/Hermes调用项目知识库SOP.md", "sop", "all", "Hermes调用项目知识库SOP"),
]


@dataclass
class Flags:
    source_root: Path
    kb_root: Path
    prompt_file: Path
    updates_file: Path | None
    max_docs: int | None
    dry_run: bool


CORE_KB_READ_ORDER = [
    "00-总览/知识库版本.md",
    "00-总览/Hermes读取入口.md",
    "00-总览/系统能力地图.md",
    "00-总览/自动化工作流总览.md",
    "00-总览/Workflow命令映射表.md",
    "00-总览/当前项目状态.md",
]


def _parse_flags(ctx: StepContext) -> Flags:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--kb-root", required=True)
    parser.add_argument("--prompt-file", default=str(PROMPT_TEMPLATE))
    parser.add_argument("--updates-file", default=None)
    parser.add_argument("--max-docs", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    return Flags(
        source_root=Path(namespace.source_root).expanduser(),
        kb_root=Path(namespace.kb_root).expanduser(),
        prompt_file=Path(namespace.prompt_file).expanduser(),
        updates_file=Path(namespace.updates_file).expanduser() if namespace.updates_file else None,
        max_docs=namespace.max_docs,
        dry_run=ctx.dry_run or namespace.dry_run,
    )


def infer_workflow_platform(workflow_id: str) -> str:
    if workflow_id.startswith("tmcs_") or workflow_id.startswith("tmall_"):
        return "tmcs"
    if workflow_id.startswith("jst_"):
        return "jst"
    if workflow_id.startswith("ai_knowledge_"):
        return "all"
    if "nas" in workflow_id:
        return "local"
    return "local"


def read_text(path: Path, *, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return default


def summarize_text(text: str, *, limit: int = 1600) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "\n...[truncated]"


def parse_workflow_name(workflow_py: Path) -> str:
    text = read_text(workflow_py)
    marker = "_make_workflow("
    index = text.find(marker)
    if index == -1:
        return workflow_py.parent.name
    snippet = text[index : index + 240]
    parts = snippet.split('"')
    if len(parts) >= 4:
        return parts[3]
    return workflow_py.parent.name


def discover_workflow_plans(source_root: Path, kb_root: Path) -> list[DocumentPlan]:
    workflows_dir = source_root / "运营自动化工具" / "workflows"
    tasks_dir = source_root / "运营自动化工具" / "tasks"
    plans: list[DocumentPlan] = []
    if not workflows_dir.exists():
        return plans
    for workflow_dir in sorted(workflows_dir.iterdir()):
        if not workflow_dir.is_dir() or workflow_dir.name.startswith("_") or workflow_dir.name == "demo":
            continue
        workflow_py = workflow_dir / "workflow.py"
        if not workflow_py.exists():
            continue
        workflow_id = workflow_dir.name
        title = parse_workflow_name(workflow_py)
        source_files = [workflow_py]
        for candidate in [
            workflow_dir / "README.md",
            workflow_dir / "steps.py",
            tasks_dir / f"{workflow_id}.yaml",
            tasks_dir / f"{workflow_id}.py",
            tasks_dir / workflow_id / "task.yaml",
            tasks_dir / workflow_id / "main.py",
            tasks_dir / workflow_id / "README.md",
        ]:
            if candidate.exists():
                source_files.append(candidate)
        plans.append(
            DocumentPlan(
                path=kb_root / "01-工作流" / f"{workflow_id}.md",
                doc_type="workflow",
                platform=infer_workflow_platform(workflow_id),
                title=title,
                source_files=source_files,
            )
        )
    return plans


def source_workflow_ids(source_root: Path) -> list[str]:
    return [plan.path.stem for plan in discover_workflow_plans(source_root, source_root / "_ignored_kb_root_placeholder")]


def discover_existing_docs(kb_root: Path, relative_dir: str, doc_type: str, platform: str) -> list[DocumentPlan]:
    base = kb_root / relative_dir
    plans: list[DocumentPlan] = []
    if not base.exists():
        return plans
    for path in sorted(base.glob("*.md")):
        plans.append(
            DocumentPlan(
                path=path,
                doc_type=doc_type,
                platform=platform,
                title=path.stem,
                source_files=[],
            )
        )
    return plans


def kb_workflow_ids(kb_root: Path) -> list[str]:
    base = kb_root / "01-工作流"
    if not base.exists():
        return []
    return sorted(path.stem for path in base.glob("*.md"))


def build_document_plans(source_root: Path, kb_root: Path) -> list[DocumentPlan]:
    plans: dict[str, DocumentPlan] = {}

    def add(relative_path: str, doc_type: str, platform: str, title: str) -> None:
        path = kb_root / relative_path
        plans[relative_path] = DocumentPlan(path=path, doc_type=doc_type, platform=platform, title=title, source_files=[])

    for item in DEFAULT_OVERVIEW_DOCS + DEFAULT_PLATFORM_DOCS + DEFAULT_SOP_DOCS:
        add(*item)

    for relative_dir, doc_type, platform in [
        ("04-项目文档", "project", "all"),
        ("07-提示词", "prompt", "all"),
        ("08-决策记录", "decision", "all"),
    ]:
        for plan in discover_existing_docs(kb_root, relative_dir, doc_type, platform):
            plans[plan.path.relative_to(kb_root).as_posix()] = plan

    for plan in discover_workflow_plans(source_root, kb_root):
        plans[plan.path.relative_to(kb_root).as_posix()] = plan

    return [plans[key] for key in sorted(plans)]


def build_version_body(
    *,
    source_root: Path,
    kb_root: Path,
    apply_result: dict,
    validation: dict,
    updated_at: str,
) -> str:
    current_ids = sorted(plan.path.stem for plan in discover_workflow_plans(source_root, kb_root))
    current_count = len(current_ids)
    added: list[str] = []
    modified: list[str] = []
    archived: list[str] = []
    for item in apply_result.get("applied_files", []):
        path = Path(item["path"])
        if path.suffix != ".md" or path.parent.name != "01-工作流":
            continue
        workflow_id = path.stem
        action = item.get("action", "upsert")
        if action == "archive":
            archived.append(workflow_id)
        elif item.get("changed"):
            if item.get("existed_before") is False:
                added.append(workflow_id)
            else:
                modified.append(workflow_id)

    validate_status = "pending"
    if validation.get("skipped"):
        validate_status = f"skipped: {validation.get('reason', '')}".strip()
    elif "success" in validation:
        validate_status = "passed" if validation["success"] else f"failed(exit={validation.get('returncode')})"

    version = datetime.now().strftime("%Y.%m.%d.%H%M%S")

    def _list_text(values: list[str]) -> str:
        return "、".join(values) if values else "无"

    read_list = "\n".join(f"{index}. `{path}`" for index, path in enumerate(CORE_KB_READ_ORDER, start=1))
    return f"""## 当前版本号

{version}

## 最后更新时间

{updated_at}

## 当前 workflow 数量

{current_count}

## 本次新增 workflow

{_list_text(sorted(set(added)))}

## 本次修改 workflow

{_list_text(sorted(set(modified)))}

## 本次废弃 workflow

{_list_text(sorted(set(archived)))}

## 更新来源

ai_knowledge_base_update workflow

## validate 状态

{validate_status}

## Hermes 读取建议

Hermes 在执行任何电商Brain / 运营店铺 / 猫超 / 聚水潭 / 自动化 workflow / 小红书内容生成 / Codex 开发交付 / Hermes 调度任务前，必须重新读取以下文件：

{read_list}

如果 Hermes memory 与 AI Knowledge Base 内容冲突，以 AI Knowledge Base 为准。"""


def write_version_doc(
    *,
    flags: Flags,
    apply_result: dict,
    validation: dict,
    dry_run: bool,
) -> dict:
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plan = DocumentPlan(
        path=flags.kb_root / "00-总览/知识库版本.md",
        doc_type="overview",
        platform="all",
        title="知识库版本",
        source_files=[],
    )
    body = build_version_body(
        source_root=flags.source_root,
        kb_root=flags.kb_root,
        apply_result=apply_result,
        validation=validation,
        updated_at=updated_at,
    )
    return apply_document_update(plan, body, date_str=updated_at[:10], dry_run=dry_run)


def build_source_manifest(source_root: Path, plans: list[DocumentPlan]) -> dict:
    workflow_items = []
    for plan in plans:
        relative_path = plan.path.as_posix()
        if "/01-工作流/" not in relative_path and not relative_path.endswith("/01-工作流"):
            continue
        workflow_items.append(
            {
                "kb_path": plan.path.name,
                "title": plan.title,
                "platform": plan.platform,
                "source_files": [str(path.relative_to(source_root)) for path in plan.source_files if path.exists()],
            }
        )
    extra_files = []
    for relative in [
        "运营自动化工具/run.py",
        "运营自动化工具/core/task_registry.py",
        "运营自动化工具/README.md",
        "Ops-Cli/README.md",
        "scripts/finalize_ai_knowledge_base.py",
        "scripts/validate_ai_knowledge_base.py",
        "scripts/sync_ai_knowledge_base.py",
    ]:
        path = source_root / relative
        if path.exists():
            extra_files.append(relative)
    return {
        "source_root": str(source_root),
        "core_read_order": list(CORE_KB_READ_ORDER),
        "workflow_docs": workflow_items,
        "readme": "README.md" if (source_root / "README.md").exists() else None,
        "docs_dir": "docs" if (source_root / "docs").exists() else None,
        "scripts_dir": "scripts" if (source_root / "scripts").exists() else None,
        "core_entry_files": extra_files,
    }


def default_bundle_dir(source_root: Path) -> Path:
    return source_root / "运营自动化工具" / "runtime" / "ai_knowledge_base_update"


def build_prompt_bundle_text(flags: Flags, plans: list[DocumentPlan], manifest: dict) -> str:
    prompt = read_text(flags.prompt_file)
    lines = [
        "# AI Knowledge Base 更新请求",
        "",
        "## 固定流程",
        "",
        "读取固定提示词 -> 读取最新项目 -> 调用当前 AI 会话理解 -> 更新 AI Knowledge Base -> 校验是否合格",
        "",
        "## 固定提示词",
        "",
        prompt.strip(),
        "",
        "## 路径",
        "",
        f"- SOURCE_ROOT: {flags.source_root}",
        f"- KB_ROOT: {flags.kb_root}",
        f"- PROMPT_FILE: {flags.prompt_file}",
        "",
        "## CORE_READ_ORDER",
        "",
        *[f"{index}. {path}" for index, path in enumerate(CORE_KB_READ_ORDER, start=1)],
        "",
        "## TARGET_FILES",
        "",
    ]
    for plan in plans:
        relative_path = plan.path.relative_to(flags.kb_root).as_posix()
        lines.append(f"- {relative_path} | type={plan.doc_type} | platform={plan.platform} | title={plan.title}")
    lines.extend(
        [
            "",
            "## SOURCE_MANIFEST",
            "",
            "```json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
            "```",
        ]
    )
    source_workflow_ids = {
        plan.path.stem
        for plan in plans
        if plan.path.parent.name == "01-工作流" and plan.source_files
    }
    existing_kb_workflow_ids = {
        path.stem for path in (flags.kb_root / "01-工作流").glob("*.md")
    } if (flags.kb_root / "01-工作流").exists() else set()
    orphan_ids = sorted(existing_kb_workflow_ids - source_workflow_ids)
    missing_ids = sorted(source_workflow_ids - existing_kb_workflow_ids)
    if missing_ids or orphan_ids:
        lines.extend(["", "## 差异提示", ""])
        if missing_ids:
            lines.append(f"- 源项目存在但知识库缺失：{', '.join(missing_ids)}")
        if orphan_ids:
            lines.append(f"- 知识库存在但源项目已无对应 workflow，建议归档：{', '.join(orphan_ids)}")
    lines.extend(
        [
            "",
            "## Hermes 强规则",
            "",
            "- Hermes 执行任何电商Brain / 运营店铺 / 猫超 / 聚水潭 / 自动化 workflow / 小红书内容生成 / Codex 开发交付 / Hermes 调度任务前，必须先重新读取 CORE_READ_ORDER。",
            "- 如果 Hermes memory 与 AI Knowledge Base 文件内容冲突，以 AI Knowledge Base 为准。",
        ]
    )
    for plan in plans[: min(len(plans), 12)]:
        if not plan.source_files:
            continue
        relative_path = plan.path.relative_to(flags.kb_root).as_posix()
        lines.extend(["", f"## TARGET_FILE: {relative_path}", ""])
        for source_file in plan.source_files[:5]:
            if not source_file.exists():
                continue
            lines.extend(
                [
                    f"### SOURCE: {source_file.relative_to(flags.source_root)}",
                    "",
                    "```text",
                    summarize_text(read_text(source_file)),
                    "```",
                    "",
                ]
            )
    return "\n".join(lines).strip() + "\n"


def load_updates_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ValueError("updates file 格式错误：缺少 documents 列表")
    return data


def run_validation(validate_script: Path, cwd: Path) -> dict:
    command = [sys.executable, str(validate_script)]
    result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "command": command,
    }


def run_dispatch_generator(source_root: Path, kb_root: Path) -> dict:
    """从各 workflow 文件 frontmatter 确定性重建派发表（见 ADR-003）。

    必须在 AI 落盘之后、校验之前调用：校验器的 dispatch/fresh 闸门要求派发表是最新产物。
    """
    gen = source_root / "scripts" / "gen_workflow_dispatch.py"
    command = [sys.executable, str(gen), "--kb-root", str(kb_root)]
    result = subprocess.run(command, cwd=str(source_root), capture_output=True, text=True)
    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "command": command,
    }


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    if not flags.source_root.exists():
        return failure_result(f"source_root 不存在：{flags.source_root}")
    if not flags.prompt_file.exists():
        return failure_result(f"固定提示词不存在：{flags.prompt_file}")
    if flags.updates_file is not None and not flags.updates_file.exists():
        return failure_result(f"updates_file 不存在：{flags.updates_file}")
    ctx.state["flags"] = flags
    return success_result(
        outputs={
            "source_root": str(flags.source_root),
            "kb_root": str(flags.kb_root),
            "prompt_file": str(flags.prompt_file),
            "updates_file": str(flags.updates_file) if flags.updates_file else None,
            "dry_run": flags.dry_run,
        }
    )


def collect_context(ctx: StepContext):
    flags: Flags = ctx.state["flags"]
    plans = build_document_plans(flags.source_root, flags.kb_root)
    if flags.max_docs is not None:
        plans = plans[: flags.max_docs]
    manifest = build_source_manifest(flags.source_root, plans)
    ctx.state["plans"] = plans
    ctx.state["manifest"] = manifest
    return success_result(
        outputs={
            "target_count": len(plans),
            "targets": [plan.path.relative_to(flags.kb_root).as_posix() for plan in plans],
        }
    )


def build_update_bundle(ctx: StepContext):
    flags: Flags = ctx.state["flags"]
    plans: list[DocumentPlan] = ctx.state["plans"]
    manifest = ctx.state["manifest"]

    bundle_dir = default_bundle_dir(flags.source_root)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = bundle_dir / "latest_update_request.md"
    manifest_path = bundle_dir / "latest_source_manifest.json"
    targets_path = bundle_dir / "latest_targets.json"

    bundle_text = build_prompt_bundle_text(flags, plans, manifest)
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    targets_json = json.dumps(
        [
            {
                "path": plan.path.relative_to(flags.kb_root).as_posix(),
                "type": plan.doc_type,
                "platform": plan.platform,
                "title": plan.title,
                "source_files": [str(path.relative_to(flags.source_root)) for path in plan.source_files if path.exists()],
            }
            for plan in plans
        ],
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    if not flags.dry_run:
        bundle_path.write_text(bundle_text, encoding="utf-8")
        manifest_path.write_text(manifest_json, encoding="utf-8")
        targets_path.write_text(targets_json, encoding="utf-8")

    artifacts = [
        Artifact(type="md", role="prompt_bundle", name=bundle_path.name, path=str(bundle_path), platform="all"),
        Artifact(type="json", role="source_manifest", name=manifest_path.name, path=str(manifest_path), platform="all"),
        Artifact(type="json", role="target_manifest", name=targets_path.name, path=str(targets_path), platform="all"),
    ]
    ctx.state["bundle"] = {
        "bundle_path": bundle_path,
        "manifest_path": manifest_path,
        "targets_path": targets_path,
    }
    return success_result(
        outputs={
            "bundle_path": str(bundle_path),
            "manifest_path": str(manifest_path),
            "targets_path": str(targets_path),
            "prompt_preview": summarize_text(bundle_text, limit=800),
        },
        artifacts=artifacts,
    )


def _fallback_plan(flags: Flags, relative_path: str) -> DocumentPlan:
    path = flags.kb_root / relative_path
    parts = Path(relative_path).parts
    doc_type = "project"
    platform = "all"
    if parts and parts[0] == "01-工作流":
        doc_type = "workflow"
        platform = infer_workflow_platform(Path(relative_path).stem)
    elif parts and parts[0] == "02-平台能力":
        doc_type = "platform"
    elif parts and parts[0] == "03-SOP":
        doc_type = "sop"
    elif parts and parts[0] == "07-提示词":
        doc_type = "prompt"
    elif parts and parts[0] == "08-决策记录":
        doc_type = "decision"
    elif parts and parts[0] == "00-总览":
        doc_type = "overview"
    return DocumentPlan(path=path, doc_type=doc_type, platform=platform, title=path.stem, source_files=[])


def apply_updates(ctx: StepContext):
    flags: Flags = ctx.state["flags"]
    plan_map = {
        plan.path.relative_to(flags.kb_root).as_posix(): plan
        for plan in ctx.state["plans"]
    }
    if flags.updates_file is None:
        result = {
            "needs_ai_apply": True,
            "changed_count": 0,
            "applied_files": [],
            "updates_file": None,
        }
        result["version_file"] = write_version_doc(
            flags=flags,
            apply_result=result,
            validation={"skipped": True, "reason": "尚未提供 updates_file"},
            dry_run=flags.dry_run,
        )
        ctx.state["apply_result"] = result
        return success_result(outputs=result)

    data = load_updates_file(flags.updates_file)
    applied = []
    for item in data["documents"]:
        relative_path = item["path"]
        body = item["auto_generated_markdown"]
        action = item.get("action", "upsert")
        frontmatter = item.get("frontmatter") if isinstance(item.get("frontmatter"), dict) else None
        plan = plan_map.get(relative_path, _fallback_plan(flags, relative_path))
        if action == "archive":
            applied.append(
                archive_document(
                    plan,
                    body,
                    archive_root=flags.kb_root / "99-归档",
                    frontmatter=frontmatter,
                    dry_run=flags.dry_run,
                )
            )
        else:
            applied.append(
                apply_document_update(
                    plan,
                    body,
                    frontmatter=frontmatter,
                    dry_run=flags.dry_run,
                )
            )
        applied[-1]["action"] = action
    result = {
        "needs_ai_apply": False,
        "changed_count": sum(1 for item in applied if item["changed"]),
        "applied_files": applied,
        "updates_file": str(flags.updates_file),
    }
    result["version_file"] = write_version_doc(
        flags=flags,
        apply_result=result,
        validation={"skipped": True, "reason": "validation pending"},
        dry_run=flags.dry_run,
    )
    ctx.state["apply_result"] = result
    return success_result(outputs=result)


def regenerate_dispatch(ctx: StepContext):
    flags: Flags = ctx.state["flags"]
    apply_result = ctx.state["apply_result"]
    if flags.dry_run:
        result = {"skipped": True, "reason": "dry-run 不重建派发表"}
        ctx.state["dispatch"] = result
        return success_result(outputs=result)
    if apply_result["needs_ai_apply"]:
        result = {"skipped": True, "reason": "尚未提供 updates_file，等待 AI 落盘后再生成"}
        ctx.state["dispatch"] = result
        return success_result(outputs=result)
    result = run_dispatch_generator(flags.source_root, flags.kb_root)
    ctx.state["dispatch"] = result
    if result["success"]:
        return success_result(outputs=result)
    return failure_result(
        [f"派发表生成失败（exit={result['returncode']}）", result.get("stderr") or result.get("stdout")],
        outputs=result,
    )


def validate_knowledge_base(ctx: StepContext):
    flags: Flags = ctx.state["flags"]
    apply_result = ctx.state["apply_result"]
    if flags.dry_run:
        validation = {"skipped": True, "reason": "dry-run 不执行校验"}
        ctx.state["validation"] = validation
        return success_result(outputs=validation)
    if apply_result["needs_ai_apply"]:
        validation = {"skipped": True, "reason": "尚未提供 updates_file，等待当前 AI 会话完成写入"}
        ctx.state["validation"] = validation
        return success_result(outputs=validation)

    validate_script = flags.source_root / "scripts" / "validate_ai_knowledge_base.py"
    result = run_validation(validate_script, flags.source_root)
    ctx.state["validation"] = result
    if result["success"]:
        return success_result(outputs=result)
    return failure_result(
        [f"知识库校验失败（exit={result['returncode']}）", result.get("stderr", "").strip() or result.get("stdout", "").strip()],
        outputs=result,
    )


def collect_outputs(ctx: StepContext):
    flags: Flags = ctx.state["flags"]
    bundle = ctx.state["bundle"]
    apply_result = ctx.state["apply_result"]
    validation = ctx.state["validation"]
    version_file = write_version_doc(
        flags=flags,
        apply_result=apply_result,
        validation=validation,
        dry_run=ctx.dry_run,
    )
    return success_result(
        outputs={
            "bundle_path": str(bundle["bundle_path"]),
            "manifest_path": str(bundle["manifest_path"]),
            "targets_path": str(bundle["targets_path"]),
            "needs_ai_apply": apply_result["needs_ai_apply"],
            "changed_count": apply_result["changed_count"],
            "applied_files": apply_result["applied_files"],
            "version_file": version_file,
            "dispatch": ctx.state.get("dispatch"),
            "validation": validation,
            "dry_run": ctx.dry_run,
        }
    )
