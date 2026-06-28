"""天猫商品价格监控——纯业务逻辑：输入商品ID解析、控价对比、状态判定。

控价不再手动输入，由 control_price_mapper 通过猫超商品列表 + 聚水潭商品资料自动匹配。
本模块只负责：解析商品ID输入、把「控价匹配结果 + 实时抓价结果」合并成最终记录。
不接触任何平台、不读控价文件（读文件在 control_price_mapper）。
"""

from __future__ import annotations

import csv
from pathlib import Path

from workflows.tmall_price_monitor.control_price_mapper import (
    STATUS_MATCHED,
    STATUS_NO_BARCODE,
    STATUS_NO_CONTROL,
    STATUS_NO_JST,
    clean_item_id,
)

# 最终状态文案。
STATUS_BELOW = "低于控价"
STATUS_NORMAL = "正常"
STATUS_PRICE_EMPTY = "价格为空"
STATUS_NOT_FOUND = "商品不存在"
STATUS_AUTH = "登录/验证码异常"
STATUS_CONTEXT_MISSING = "价格上下文缺失"
STATUS_FAILED = "抓取失败"

# 抓价 capture_status -> 最终状态（仅当控价匹配成功、但实时价拿不到时使用）。
_CAPTURE_FAIL_MAP = {
    "login_required": STATUS_AUTH,
    "captcha": STATUS_AUTH,
    "item_not_found": STATUS_NOT_FOUND,
    "price_context_missing": STATUS_CONTEXT_MISSING,
    "price_empty": STATUS_PRICE_EMPTY,
    "failed": STATUS_FAILED,
}


class InputError(ValueError):
    """输入参数 / 文件相关的可读错误。"""


def _split_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.replace("，", ",").split(",") if piece.strip()]


def load_item_ids_from_csv(csv_path: str | Path) -> list[str]:
    """从 CSV 读取商品ID。只需 item_id 列（兼容旧的 item_id,control_price 表，忽略控价列）。"""
    path = Path(csv_path).expanduser()
    if not path.exists():
        raise InputError(f"输入文件不存在：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    id_col = 0
    start = 0
    for key in ("item_id", "商品id", "id"):
        if key in header:
            id_col = header.index(key)
            start = 1
            break
    else:
        # 无表头：首行若是纯数字则当数据，否则当表头跳过。
        first = (rows[0][0] if rows[0] else "").strip()
        start = 0 if clean_item_id(first).isdigit() else 1

    ids: list[str] = []
    for raw in rows[start:]:
        if not raw or id_col >= len(raw):
            continue
        item_id = clean_item_id(raw[id_col])
        if item_id:
            ids.append(item_id)
    return ids


def resolve_item_ids(
    *,
    item_id: str | None,
    item_ids: str | None,
    file: str | Path | None,
) -> list[str]:
    """汇总待检商品ID（去重保序，清洗为字符串）。"""
    collected: list[str] = []
    collected.extend(_split_ids(item_ids))
    if item_id and item_id.strip():
        collected.append(item_id.strip())
    if file:
        collected.extend(load_item_ids_from_csv(file))

    cleaned = [clean_item_id(x) for x in collected]
    cleaned = [x for x in cleaned if x]
    deduped = list(dict.fromkeys(cleaned))
    if not deduped:
        raise InputError("请至少提供 --item-id / --item-ids 之一，或用 --file 指定含 item_id 列的 CSV。")
    return deduped


def build_record(scrape_row: dict | None, mapping: dict) -> dict:
    """合并「控价匹配结果」与「实时抓价结果」成最终记录（含状态与差价）。"""
    scrape_row = scrape_row or {}
    item_id = mapping.get("item_id") or str(scrape_row.get("item_id") or "")
    control = mapping.get("taoxi_control_price")
    mapping_status = mapping.get("mapping_status", STATUS_NO_BARCODE)

    realtime = scrape_row.get("realtime_price")
    realtime = round(float(realtime), 2) if isinstance(realtime, (int, float)) else None
    capture_status = scrape_row.get("capture_status")
    title = str(scrape_row.get("title") or mapping.get("maochao_name") or "")

    diff_price: float | None = None
    if mapping_status != STATUS_MATCHED:
        # 控价匹配未成功：直接报匹配阶段状态（未找到猫超条码/未找到聚水潭商品/控价为空）。
        status = {
            STATUS_NO_BARCODE: STATUS_NO_BARCODE,
            STATUS_NO_JST: STATUS_NO_JST,
            STATUS_NO_CONTROL: STATUS_NO_CONTROL,
        }.get(mapping_status, STATUS_FAILED)
    elif capture_status == "ok" and realtime is not None and control is not None:
        diff_price = round(realtime - control, 2)
        status = STATUS_BELOW if realtime < control else STATUS_NORMAL
    else:
        status = _CAPTURE_FAIL_MAP.get(str(capture_status), STATUS_FAILED)

    return {
        "item_id": item_id,
        "title": title,
        "barcode": mapping.get("barcode", ""),
        "jst_goods_code": mapping.get("jst_goods_code", ""),
        "jst_goods_name": mapping.get("jst_goods_name", ""),
        "taoxi_control_price": control,
        "realtime_price": realtime,
        "diff_price": diff_price,
        "status": status,
        "captured_at": str(scrape_row.get("captured_at") or ""),
        "screenshot_path": str(scrape_row.get("screenshot_path") or ""),
        "capture_status": capture_status,
        "raw_price_text": str(scrape_row.get("raw_price_text") or ""),
        "mapping_status": mapping_status,
        "matched_barcode_count": mapping.get("matched_barcode_count", 0),
        "all_control_prices": mapping.get("all_control_prices", []),
        "error": scrape_row.get("error"),
    }


def summarize(records: list[dict]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for record in records:
        status = record["status"]
        summary[status] = summary.get(status, 0) + 1
    return summary
