"""价格竞争力「整张列表」本地缓存 + 批量匹配（纯业务逻辑，便于单测）。

缓存策略：按天。缓存文件以 list_date（猫超「每日跟价商品」日期，默认当天）命名，
当天首次查询抓一次整张列表落缓存，当天后续全部走缓存；跨天 / --refresh 时重抓。

缓存目录：runtime/cache/tmcs_price_competitiveness/（runtime/ 已 gitignore）。
本模块不碰平台，只读写本地 JSON、做编码精确匹配。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.config_loader import get_path


def cache_dir() -> Path:
    return Path(get_path("runtime_dir")) / "cache" / "tmcs_price_competitiveness"


def cache_path(list_date: str) -> Path:
    safe = str(list_date).strip() or "unknown"
    return cache_dir() / f"list_{safe}.json"


def load_cache(list_date: str) -> dict[str, Any] | None:
    """读取指定日期的缓存；不存在 / 损坏返回 None。"""
    path = cache_path(list_date)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return None
    return payload


def save_cache(list_date: str, snapshot: dict[str, Any]) -> Path:
    """把整张列表快照写入当天缓存文件，返回路径。"""
    path = cache_path(list_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "list_date": list_date,
        "captured_at": snapshot.get("captured_at"),
        "total_rows": snapshot.get("total_rows"),
        "rows": snapshot.get("rows") or [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def parse_codes(
    *,
    product_code: str | None,
    product_codes: str | None,
    codes_file: str | None,
) -> list[str]:
    """合并 --product-code / --product-codes / --codes-file，去重保序。"""
    raw: list[str] = []
    if product_code:
        raw.append(product_code)
    if product_codes:
        raw.extend(re.split(r"[,，\s]+", product_codes))
    if codes_file:
        path = Path(codes_file).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"商品编码文件不存在：{path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            raw.append(line)

    seen: set[str] = set()
    codes: list[str] = []
    for item in raw:
        code = item.strip()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def match_code(code: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """逐行精确匹配单个商品编码（item_id == code），返回 exists + matched_items。"""
    target = str(code).strip()
    matched: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for row in rows or []:
        item_id = str(row.get("item_id") or "").strip()
        if item_id != target:
            continue
        key = (item_id, row.get("sku_id"))
        if key in seen:
            continue
        seen.add(key)
        matched.append(
            {"item_id": item_id, "sku_id": row.get("sku_id"), "title": row.get("title")}
        )
    return {"product_code": target, "exists": bool(matched), "matched_items": matched}


def match_codes(codes: list[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量匹配一组商品编码。"""
    return [match_code(code, rows) for code in codes]
