from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


AUTO_START = "<!-- AUTO-GENERATED:START -->"
AUTO_END = "<!-- AUTO-GENERATED:END -->"


@dataclass
class DocumentPlan:
    path: Path
    doc_type: str
    platform: str
    title: str
    source_files: list[Path] = field(default_factory=list)
    status: str = "active"

    @property
    def relative_path(self) -> str:
        return self.path.as_posix()


FRONTMATTER_ORDER = [
    "type",
    "status",
    "updated",
    "source",
    "workflow_id",
    "cn_name",
    "platform",
    "hermes_auto",
    "dry_run_cmd",
    "run_cmd",
    "triggers",
]
QUOTED_STRING_KEYS = {"cn_name", "dry_run_cmd", "run_cmd"}


def parse_frontmatter_fields(frontmatter: str | None) -> dict[str, Any]:
    if not frontmatter:
        return {}
    fields: dict[str, Any] = {}
    for raw_line in frontmatter.strip().splitlines():
        line = raw_line.strip()
        if not line or line == "---" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in {"true", "false"}:
            fields[key] = value == "true"
        elif value.startswith("[") and value.endswith("]"):
            try:
                fields[key] = json.loads(value)
            except json.JSONDecodeError:
                fields[key] = re.findall(r'"((?:[^"\\]|\\.)*)"', value)
        else:
            fields[key] = value.strip('"')
    return fields


def format_frontmatter_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if value is None:
        return ""
    text = str(value)
    if key in QUOTED_STRING_KEYS:
        return json.dumps(text, ensure_ascii=False)
    if any(ch.isspace() for ch in text) or any(ch in text for ch in [":", "#", "[", "]", "{", "}", '"']):
        return json.dumps(text, ensure_ascii=False)
    return text


def build_frontmatter(plan: DocumentPlan, date_str: str, *, fields: dict[str, Any] | None = None) -> str:
    merged: dict[str, Any] = dict(fields or {})
    merged.update(
        {
            "type": plan.doc_type,
            "status": plan.status,
            "updated": date_str,
            "source": "ai-updated",
        }
    )
    merged.setdefault("platform", plan.platform)
    if plan.doc_type == "workflow":
        merged["workflow_id"] = plan.path.stem
    ordered_keys = [key for key in FRONTMATTER_ORDER if key in merged]
    ordered_keys.extend(key for key in merged if key not in FRONTMATTER_ORDER)
    lines = ["---"]
    lines.extend(f"{key}: {format_frontmatter_value(key, merged[key])}" for key in ordered_keys)
    lines.append("---")
    return "\n".join(lines)


def split_frontmatter(content: str) -> tuple[str | None, str]:
    if not content.startswith("---\n"):
        return None, content
    end = content.find("\n---", 4)
    if end == -1:
        return None, content
    return content[: end + 4], content[end + 4 :].lstrip("\n")


def replace_auto_section(content: str, body: str) -> str:
    start = content.find(AUTO_START)
    end = content.find(AUTO_END)
    if start == -1 or end == -1 or end < start:
        return insert_auto_section(content, body)
    before = content[: start + len(AUTO_START)]
    after = content[end:]
    return before + "\n\n" + body.strip() + "\n\n" + after


def insert_auto_section(content: str, body: str) -> str:
    stripped = content.lstrip()
    title_match = re.match(r"^# .+$", stripped, re.MULTILINE)
    block = f"{AUTO_START}\n\n{body.strip()}\n\n{AUTO_END}\n"
    if title_match:
        title = title_match.group(0)
        index = stripped.find(title) + len(title)
        prefix_len = len(content) - len(stripped)
        insert_at = prefix_len + index
        return content[:insert_at] + "\n\n" + block + "\n" + content[insert_at:].lstrip("\n")
    return f"{block}\n{content.lstrip()}"


def ensure_title(content: str, title: str) -> str:
    stripped = content.lstrip()
    if stripped.startswith("# "):
        return content
    return f"# {title}\n\n{content.lstrip()}"


def apply_document_update(
    plan: DocumentPlan,
    auto_body: str,
    *,
    date_str: str | None = None,
    frontmatter: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict:
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    path = Path(plan.path)
    existed_before = path.exists()
    existing = path.read_text(encoding="utf-8") if existed_before else ""
    existing_frontmatter, body = split_frontmatter(existing)
    frontmatter_fields = parse_frontmatter_fields(existing_frontmatter)
    if frontmatter:
        frontmatter_fields.update(frontmatter)

    backup_path = None
    if existing and AUTO_START not in existing and AUTO_END not in existing:
        backup_path = path.with_suffix(path.suffix + f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}")

    updated_body = ensure_title(body, plan.title)
    updated_body = replace_auto_section(updated_body, auto_body)
    new_content = build_frontmatter(plan, date_str, fields=frontmatter_fields) + "\n\n" + updated_body.rstrip() + "\n"
    changed = new_content != existing

    if not dry_run and changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        if backup_path is not None and existing:
            backup_path.write_text(existing, encoding="utf-8")
        path.write_text(new_content, encoding="utf-8")

    return {
        "path": str(path),
        "changed": changed,
        "existed_before": existed_before,
        "backup_path": str(backup_path) if backup_path is not None else None,
        "dry_run": dry_run,
    }


def archive_document(
    plan: DocumentPlan,
    auto_body: str,
    *,
    archive_root: Path,
    date_str: str | None = None,
    frontmatter: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict:
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    source_path = Path(plan.path)
    existed_before = source_path.exists()
    relative = source_path.relative_to(archive_root.parent) if archive_root.parent in source_path.parents else Path(source_path.name)
    target_path = archive_root / relative
    existing = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    updated_body = existing
    if existing:
        existing_frontmatter, body = split_frontmatter(existing)
        frontmatter_fields = parse_frontmatter_fields(existing_frontmatter)
        updated_body = ensure_title(body, plan.title)
        updated_body = replace_auto_section(updated_body, auto_body)
    else:
        frontmatter_fields = {}
        updated_body = ensure_title("", plan.title)
        updated_body = replace_auto_section(updated_body, auto_body)
    if frontmatter:
        frontmatter_fields.update(frontmatter)
    new_content = build_frontmatter(
        DocumentPlan(path=target_path, doc_type=plan.doc_type, platform=plan.platform, title=plan.title, status="archived"),
        date_str,
        fields=frontmatter_fields,
    ) + "\n\n" + updated_body.rstrip() + "\n"
    changed = True
    if not dry_run:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_content, encoding="utf-8")
        if source_path.exists():
            source_path.unlink()
    return {
        "path": str(source_path),
        "changed": changed,
        "existed_before": existed_before,
        "archived_path": str(target_path),
        "dry_run": dry_run,
    }
