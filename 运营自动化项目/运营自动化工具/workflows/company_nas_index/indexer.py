"""公司网盘产品资料索引的纯业务逻辑：目录扫描、汇总、JSON/MD/CSV 写出、搜索打分。

只做内存计算与本地索引文件读写，NAS 挂载/根目录来自 workflows.company_nas_common.nas，
平台无关。本模块不做 workflow 编排，也不移动/删除 NAS 文件。
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config_loader import get_path
from workflows.company_nas_common.nas import BRAND_FOLDERS, SKIP_NAMES, normalize_code_text

INDEX_DIR = get_path("nas_index_dir")
JSON_PATH = get_path("nas_index_json")
MD_PATH = get_path("nas_index_md")
CSV_PATH = get_path("nas_index_csv")
CHECKPOINT_PATH = INDEX_DIR / "company_nas_scan_checkpoint.jsonl"
HEAVY_SUFFIXES = {".psd", ".psb", ".mp4", ".mov", ".m4v", ".avi", ".zip", ".rar", ".7z"}


def norm_text(value: object) -> str:
    return normalize_code_text(value)


def brand_from_parts(parts: tuple[str, ...]) -> str:
    if not parts:
        return ""
    folder = parts[0]
    for brand, brand_folder in BRAND_FOLDERS.items():
        if folder == brand_folder or brand in folder:
            return brand
    return folder.split(".", 1)[-1] if "." in folder else folder


def category_from_parts(parts: tuple[str, ...]) -> str:
    if len(parts) < 2:
        return ""
    return parts[1]


def model_from_parts(parts: tuple[str, ...], item_name: str, item_type: str) -> str:
    if len(parts) >= 3:
        return parts[2]
    if item_type == "dir":
        return item_name
    return ""


def keywords_for(*values: object) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        candidates = {text, norm_text(text)}
        candidates.update(part for part in re.split(r"[\s_./\\()（）【】\[\]-]+", text) if part)
        for candidate in candidates:
            normalized = str(candidate).strip()
            if normalized and normalized not in seen:
                keywords.append(normalized)
                seen.add(normalized)
    return keywords


def record_for(path: Path, root: Path, item_type: str) -> dict[str, Any]:
    rel = path.relative_to(root)
    parts = rel.parts
    stat = path.stat()
    brand = brand_from_parts(parts)
    category = category_from_parts(parts)
    model_or_folder = model_from_parts(parts, path.name, item_type)
    suffix = path.suffix.lower() if item_type == "file" else ""
    parent = str(path.parent)
    return {
        "brand": brand,
        "category": category,
        "model_or_folder": model_or_folder,
        "path": str(path),
        "type": item_type,
        "filename": path.name if item_type == "file" else "",
        "suffix": suffix,
        "size": stat.st_size if item_type == "file" else 0,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "depth": len(parts),
        "parent": parent,
        "keywords": keywords_for(brand, category, model_or_folder, path.name, rel),
    }


def append_checkpoint(handle: Any, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    handle.flush()


def scan_index(root: Path, *, max_depth: int, include_files: bool) -> list[dict[str, Any]]:
    if not root.is_dir():
        raise SystemExit(f"NAS 产品资料根目录不存在：{root}")
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
    records: list[dict[str, Any]] = []
    with CHECKPOINT_PATH.open("w", encoding="utf-8") as checkpoint:
        for current, dirs, files in os.walk(root):
            dirs[:] = [name for name in dirs if name not in SKIP_NAMES]
            current_path = Path(current)
            depth = len(current_path.relative_to(root).parts) if current_path != root else 0
            if max_depth > 0 and depth >= max_depth:
                dirs[:] = []
            if current_path != root and (max_depth <= 0 or depth <= max_depth):
                try:
                    record = record_for(current_path, root, "dir")
                    records.append(record)
                    append_checkpoint(checkpoint, record)
                except OSError as exc:
                    logging.warning("skip dir %s: %s", current_path, exc)
            if not include_files:
                continue
            for name in files:
                if name in SKIP_NAMES:
                    continue
                path = current_path / name
                try:
                    record = record_for(path, root, "file")
                    records.append(record)
                    append_checkpoint(checkpoint, record)
                except OSError as exc:
                    logging.warning("skip file %s: %s", path, exc)
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    dirs = [item for item in records if item["type"] == "dir"]
    files = [item for item in records if item["type"] == "file"]
    return {
        "dir_count": len(dirs),
        "file_count": len(files),
        "brand_count": len({item["brand"] for item in records if item.get("brand")}),
        "category_count": len({(item["brand"], item["category"]) for item in records if item.get("category")}),
        "heavy_file_count": sum(1 for item in files if item.get("suffix") in HEAVY_SUFFIXES),
    }


def write_json(root: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "summary": summary,
        "records": records,
    }
    JSON_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(records: list[dict[str, Any]]) -> None:
    fields = ["brand", "category", "model_or_folder", "path", "type", "filename", "suffix", "size", "mtime", "depth", "parent", "keywords"]
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = dict(record)
            row["keywords"] = " ".join(record.get("keywords") or [])
            writer.writerow({field: row.get(field, "") for field in fields})


def write_md(root: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    dirs = [item for item in records if item["type"] == "dir"]
    lines = [
        "# 公司 NAS 产品资料目录索引",
        "",
        f"- 根目录：`{root}`",
        f"- 目录数：{summary['dir_count']}",
        f"- 文件数：{summary['file_count']}",
        f"- 品牌数：{summary['brand_count']}",
        f"- 类目数：{summary['category_count']}",
        "",
        "## 品牌/类目/型号目录",
        "",
    ]
    for record in sorted(dirs, key=lambda item: (item["brand"], item["category"], item["depth"], item["path"])):
        if record["depth"] > 4:
            continue
        indent = "  " * max(record["depth"] - 1, 0)
        label = Path(record["path"]).name
        lines.append(f"{indent}- {label} `{record['path']}`")
    MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_index() -> dict[str, Any]:
    if not JSON_PATH.exists():
        raise SystemExit(f"索引文件不存在：{JSON_PATH}\n请先运行：python3 run.py 更新公司网盘索引")
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def score_record(record: dict[str, Any], query: str) -> tuple[int, list[str]]:
    normalized_query = norm_text(query)
    raw_query = query.lower()
    score = 0
    reasons: list[str] = []
    fields = {
        "品牌": record.get("brand", ""),
        "类目": record.get("category", ""),
        "型号/文件夹": record.get("model_or_folder", ""),
        "文件名": record.get("filename", ""),
        "路径": record.get("path", ""),
    }
    matched = False
    for label, value in fields.items():
        text = str(value or "")
        normalized_text = norm_text(text)
        if normalized_query and normalized_query == normalized_text:
            score += 100
            matched = True
            reasons.append(f"{label}精确匹配")
        elif normalized_query and normalized_query in normalized_text:
            score += 50
            matched = True
            reasons.append(f"{label}包含关键词")
        elif raw_query and raw_query in text.lower():
            score += 30
            matched = True
            reasons.append(f"{label}文本匹配")
    if not matched:
        return 0, []
    if record.get("type") == "dir":
        score += 15
        reasons.append("优先产品文件夹")
    if int(record.get("depth") or 0) == 3:
        score += 15
        reasons.append("疑似型号目录")
    return score, reasons


def search_index(query: str, *, limit: int) -> dict[str, Any]:
    payload = load_index()
    matches = []
    for record in payload.get("records") or []:
        score, reasons = score_record(record, query)
        if score <= 0:
            continue
        matches.append({**record, "score": score, "match_reason": "；".join(dict.fromkeys(reasons))})
    matches.sort(key=lambda item: (-item["score"], item["type"] != "dir", item["depth"], item["path"]))
    return {
        "query": query,
        "index_path": str(JSON_PATH),
        "match_count": len(matches),
        "matches": matches[:limit],
    }
