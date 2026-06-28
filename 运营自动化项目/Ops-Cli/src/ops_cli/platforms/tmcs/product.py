from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from openpyxl import load_workbook

from ops_cli.capabilities import mark_scene_refreshed
from ops_cli.capabilities import require_interactive_recovery
from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.platforms.auth_shared import is_probable_auth_error
from ops_cli.runtime_context import write_runtime_context

from ops_cli.platforms.tmcs.shared import TMCS_PRODUCT_EXPORT_FILENAME
from ops_cli.platforms.tmcs.shared import TMCS_PRODUCT_EXPORT_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_PRODUCT_LATEST_FILENAME
from ops_cli.platforms.tmcs.shared import TMCS_PRODUCT_SEARCH_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_SITE
from ops_cli.platforms.tmcs.shared import check_scene_or_fail
from ops_cli.platforms.tmcs.shared import ensure_scene_assets
from ops_cli.platforms.tmcs.shared import extract_export_task_id
from ops_cli.platforms.tmcs.shared import filter_cookies_for_url
from ops_cli.platforms.tmcs.shared import find_download_url
from ops_cli.platforms.tmcs.shared import form_encode
from ops_cli.platforms.tmcs.shared import gei_task_download_url
from ops_cli.platforms.tmcs.shared import is_probably_excel
from ops_cli.platforms.tmcs.shared import load_scene_or_fail
from ops_cli.platforms.tmcs.shared import read_json
from ops_cli.platforms.tmcs.shared import resolve_download_content
from ops_cli.platforms.tmcs.shared import sanitize_replay_headers
from ops_cli.platforms.tmcs.shared import tmcs_download
from ops_cli.platforms.tmcs.shared import tmcs_request
from ops_cli.platforms.tmcs.shared import write_json
from ops_cli.platforms.tmcs.shared import merge_cookie_header


TEMPLATE_PATH = Path("data/tmcs/product_sync_template.json")
TMCS_MASTER_HEADERS = [
    "商品编码",
    "商品名称",
    "商品上下架状态",
    "SKU编码",
    "SKU上下架状态",
    "生产厂家",
    "条码",
    "零售价(元)",
    "建档供应商名称",
    "所属店铺",
    "货品编码",
    "淘系品牌ID",
    "淘系品牌名称",
    "自营品牌ID",
    "自营品牌名称",
    "淘系类目名称",
    "创建时间",
    "销售类型",
    "商品类型",
    "一盘货状态",
    "审核状态",
    "采购负责人",
    "建档供应商编码",
    "一级自营类目ID",
    "一级自营类目名称",
    "二级自营类目ID",
    "二级自营类目名称",
    "三级自营类目ID",
    "三级自营类目名称",
    "自营类目ID",
    "自营类目名称",
    "类目ID",
    "数据来源",
]


def _template_path() -> Path:
    return Path.cwd() / TEMPLATE_PATH


def _write_template(*, export_scene: dict[str, Any], search_scene: dict[str, Any]) -> Path:
    config = get_config()
    template = {
        "site": TMCS_SITE,
        "scenes": {
            "search": TMCS_PRODUCT_SEARCH_SCENE,
            "export": TMCS_PRODUCT_EXPORT_SCENE,
        },
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "defaults": {
            "import_path": config.tmcs_product_import_path,
            "latest_path": config.tmcs_product_latest_path,
            "jst_path": config.jst_product_source_path,
            "import_file_name": TMCS_PRODUCT_EXPORT_FILENAME,
            "latest_file_name": TMCS_PRODUCT_LATEST_FILENAME,
        },
        "search": {
            "url": search_scene.get("url"),
            "method": search_scene.get("method"),
            "headers": _sanitize_tmcs_headers(
                search_scene.get("headers") or {},
                search_scene.get("cookies") or [],
                target_url=str(search_scene.get("url") or ""),
            ),
            "post_data_form": search_scene.get("post_data_form") or {},
        },
        "export": {
            "url": export_scene.get("url"),
            "method": export_scene.get("method"),
            "headers": _sanitize_tmcs_headers(
                export_scene.get("headers") or {},
                export_scene.get("cookies") or [],
                target_url=str(export_scene.get("url") or ""),
            ),
            "post_data_form": export_scene.get("post_data_form") or {},
        },
    }
    path = _template_path()
    write_json(path, template)
    return path


def _sanitize_tmcs_headers(
    headers: dict[str, Any],
    cookies: list[dict[str, Any]] | None = None,
    *,
    target_url: str = "",
) -> dict[str, str]:
    cleaned = sanitize_replay_headers(headers, [])
    cookie_header = merge_cookie_header({}, filter_cookies_for_url(cookies, target_url)).get("cookie")
    if cookie_header:
        cleaned["cookie"] = cookie_header
    return cleaned


def _load_template() -> dict[str, Any]:
    path = _template_path()
    if not path.exists():
        raise RuntimeError(f"未找到猫超商品同步模板：{path}。请先运行 `ops tmcs product learn`。")
    return read_json(path)


def _norm(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text.upper()


def _load_sheet_data(path: Path, sheet_name: str | None = None) -> tuple[list[str], list[list[object]]]:
    workbook = load_workbook(path, data_only=True)
    worksheet = workbook[sheet_name] if sheet_name else workbook[workbook.sheetnames[0]]
    header = [
        str(worksheet.cell(1, column).value).strip() if worksheet.cell(1, column).value is not None else ""
        for column in range(1, worksheet.max_column + 1)
    ]
    rows: list[list[object]] = []
    for row_index in range(2, worksheet.max_row + 1):
        row = [worksheet.cell(row_index, column).value for column in range(1, worksheet.max_column + 1)]
        if any(value not in (None, "") for value in row):
            rows.append(row)
    workbook.close()
    return header, rows


def _require_columns(header: list[str], required: list[str], file_name: str) -> None:
    missing = [column for column in required if column not in header]
    if missing:
        raise RuntimeError(f"{file_name} 缺少必要字段: {', '.join(missing)}")


def _column_index(header: list[str], candidates: list[str], file_name: str) -> int:
    for candidate in candidates:
        if candidate in header:
            return header.index(candidate)
    raise RuntimeError(f"{file_name} 缺少必要字段: {'/'.join(candidates)}")


def _optional_column_index(header: list[str], candidates: list[str]) -> int | None:
    for candidate in candidates:
        if candidate in header:
            return header.index(candidate)
    return None


def _cell(row: list[object], index: int | None) -> object | None:
    if index is None or index >= len(row):
        return None
    return row[index]


def _build_row_by_latest_header(latest_header: list[str], import_header: list[str], import_row: list[object]) -> list[object]:
    import_index = {name: idx for idx, name in enumerate(import_header)}
    row: list[object] = []
    for column_name in latest_header:
        idx = import_index.get(column_name)
        row.append(import_row[idx] if idx is not None and idx < len(import_row) else None)
    return row


def _tmcs_status_text(value: object) -> str | None:
    if value in (1, "1", True):
        return "上架"
    if value in (-1, "-1", 0, "0", False):
        return "下架"
    text = _norm(value)
    if text in {"UP", "ON_SHELF"}:
        return "上架"
    if text in {"DOWN", "OFF_SHELF"}:
        return "下架"
    return None


def _tmcs_excel_time(value: object) -> str | None:
    if value in (None, ""):
        return None
    try:
        timestamp = float(str(value))
    except (TypeError, ValueError):
        return str(value)
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _first_non_empty(*values: object) -> object | None:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _parse_sku_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    sku_rows = item.get("skuVOList")
    if isinstance(sku_rows, list):
        return [sku for sku in sku_rows if isinstance(sku, dict)]
    return []


def _build_tmcs_master_row(item: dict[str, Any], sku: dict[str, Any] | None) -> list[object]:
    self_category_id = _first_non_empty(item.get("selfCategoryId"), item.get("categoryId"))
    self_category_name = _first_non_empty(item.get("selfCategoryName"), item.get("categoryName"))
    sku = sku or {}
    reserve_price = _first_non_empty(sku.get("reservePriceCNY"), item.get("reservePriceCNY"), item.get("reservePrice"))
    if isinstance(reserve_price, (int, float)) and reserve_price > 10_000:
        reserve_price = reserve_price / 100
    product_type = _first_non_empty(item.get("itemChannelType"), item.get("itemType"))
    return [
        item.get("itemId"),
        item.get("title"),
        _tmcs_status_text(item.get("updownStatus")),
        _first_non_empty(sku.get("skuId"), sku.get("taoSkuId")),
        _tmcs_status_text(_first_non_empty(sku.get("updownStatus"), sku.get("mainSkuUpdownStatus"))),
        item.get("storeName"),
        _first_non_empty(sku.get("barcode"), item.get("barcode")),
        reserve_price,
        item.get("supplierName"),
        _first_non_empty(item.get("shopName"), item.get("storeName")),
        _first_non_empty(sku.get("targetScItemId"), item.get("storageGoodsId"), item.get("erpCode")),
        item.get("brandId"),
        item.get("brandName"),
        item.get("selfBrandId"),
        item.get("selfBrandName"),
        item.get("categoryName"),
        _tmcs_excel_time(item.get("gmtCreate")),
        item.get("itemChannelType"),
        product_type,
        item.get("stockShareStatus"),
        _first_non_empty(item.get("auditStatusDesc"), item.get("auctionSubStatusDesc")),
        item.get("categoryManager"),
        item.get("supplierCode"),
        None,
        None,
        None,
        None,
        None,
        None,
        self_category_id,
        self_category_name,
        item.get("categoryId"),
        "product_search_api_fallback",
    ]


def _build_tmcs_master_rows(items: list[dict[str, Any]]) -> list[list[object]]:
    rows: list[list[object]] = []
    for item in items:
        sku_rows = _parse_sku_rows(item)
        if sku_rows:
            for sku in sku_rows:
                rows.append(_build_tmcs_master_row(item, sku))
        else:
            rows.append(_build_tmcs_master_row(item, None))
    return rows


def _build_jst_code_pool(jst_rows: list[list[object]], jst_header: list[str]) -> tuple[dict[str, str], list[str]]:
    product_code_idx = jst_header.index("商品编码")
    exact_map: dict[str, str] = {}
    normalized_codes: list[str] = []
    seen: set[str] = set()
    for row in jst_rows:
        code = _norm(row[product_code_idx] if product_code_idx < len(row) else None)
        if not code:
            continue
        exact_map.setdefault(code, code)
        if code not in seen:
            normalized_codes.append(code)
            seen.add(code)
    return exact_map, normalized_codes


def _find_replacement_code(barcode: object, exact_map: dict[str, str], normalized_codes: list[str]) -> tuple[str | None, str]:
    normalized_barcode = _norm(barcode)
    if not normalized_barcode:
        return None, "empty"
    exact_hit = exact_map.get(normalized_barcode)
    if exact_hit is not None:
        return exact_hit, "exact"
    fuzzy_hits = [code for code in normalized_codes if normalized_barcode in code or code in normalized_barcode]
    if len(fuzzy_hits) == 1:
        return fuzzy_hits[0], "fuzzy"
    if len(fuzzy_hits) > 1:
        return None, "multiple"
    return None, "miss"


def _save_latest_rows(path: Path, rows: list[list[object]]) -> None:
    workbook = load_workbook(path)
    worksheet = workbook[workbook.sheetnames[0]]
    max_existing_row = worksheet.max_row
    if max_existing_row > 1:
        worksheet.delete_rows(2, max_existing_row - 1)
    for row in rows:
        worksheet.append(row)
    workbook.save(path)
    workbook.close()


def _flatten_row(row: Any, *, prefix: str = "") -> dict[str, Any]:
    if not isinstance(row, dict):
        return {prefix or "value": row}
    flattened: dict[str, Any] = {}
    for key, value in row.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_row(value, prefix=name))
        elif isinstance(value, list):
            flattened[name] = json.dumps(value, ensure_ascii=False)
        else:
            flattened[name] = value
    return flattened


def _extract_rows(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)], None
    if not isinstance(payload, dict):
        return [], None
    data = payload.get("data", payload)
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)], None
    if not isinstance(data, dict):
        return [], None
    total = None
    for key in ("total", "totalCount", "count"):
        if isinstance(data.get(key), int):
            total = int(data[key])
            break
    for key in ("data", "list", "rows", "records", "items", "dataSource"):
        rows = data.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)], total
    return [], total


def _first_text(*values: object) -> str:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text
    return ""


def build_tmall_activity_url_from_tmcs_row(row: dict[str, Any], *, item_id: str | None = None) -> str:
    """从猫超商品列表 searchItem 行里构造带 mi_id 的天猫详情链接。"""
    if not isinstance(row, dict):
        return ""
    resolved_item_id = _first_text(item_id, row.get("itemId"), row.get("id"), row.get("item_id"))
    if not resolved_item_id:
        return ""

    for value in _iter_text_values(row):
        if "detail.tmall.com" in value and f"id={resolved_item_id}" in value and "mi_id=" in value:
            return value if value.startswith(("http://", "https://")) else f"https:{value}" if value.startswith("//") else value

    attr_map = row.get("attributeMap") if isinstance(row.get("attributeMap"), dict) else {}
    mid = _first_text(row.get("mi_id"), row.get("miId"), row.get("mid"), attr_map.get("mi_id"), attr_map.get("miId"), attr_map.get("mid"))
    if not mid:
        return ""
    return f"https://detail.tmall.com/item.htm?{urlencode({'id': resolved_item_id, 'mi_id': mid})}"


def _iter_text_values(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_text_values(child)
        return
    if isinstance(value, list):
        for child in value:
            yield from _iter_text_values(child)
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text


def query_tmall_activity_urls(item_ids: list[str]) -> dict[str, str]:
    """用猫超商品列表 searchItem 批量查商品 mid，并返回可用于买家端抓价的完整链接。"""
    cleaned_ids = [item_id for item_id in dict.fromkeys(_first_text(item_id) for item_id in item_ids) if item_id]
    if not cleaned_ids:
        return {}
    template = _load_template()
    search_scene = template.get("search") or {}
    form_data = dict(search_scene.get("post_data_form") or {})
    headers = dict(search_scene.get("headers") or {})
    method = str(search_scene.get("method") or "POST").upper()
    url = str(search_scene.get("url") or "").strip()
    if not url:
        return {}
    form_data["itemIds"] = ",".join(cleaned_ids)
    form_data["pageIndex"] = "1"
    form_data["pageSize"] = str(max(20, len(cleaned_ids)))
    _, payload, _ = tmcs_request(method, url, headers=headers, data_body=form_encode(form_data), timeout=60.0)
    rows, _ = _extract_rows(payload)
    result: dict[str, str] = {}
    wanted = set(cleaned_ids)
    for row in rows:
        row_item_id = _first_text(row.get("itemId"), row.get("id"), row.get("item_id"))
        if row_item_id not in wanted:
            continue
        activity_url = build_tmall_activity_url_from_tmcs_row(row, item_id=row_item_id)
        if activity_url:
            result.setdefault(row_item_id, activity_url)
    return result


def _download_goods_from_search(*, search_scene: dict[str, Any], destination: Path) -> dict[str, Any]:
    from openpyxl import Workbook

    form_data = dict(search_scene.get("post_data_form") or {})
    headers = dict(search_scene.get("headers") or {})
    method = str(search_scene.get("method") or "POST").upper()
    url = str(search_scene.get("url") or "").strip()
    form_data["pageSize"] = "100"
    all_rows: list[dict[str, Any]] = []
    total: int | None = None
    for page_index in range(1, 1000):
        form_data["pageIndex"] = str(page_index)
        _, payload, _ = tmcs_request(method, url, headers=headers, data_body=form_encode(form_data), timeout=120.0)
        rows, detected_total = _extract_rows(payload)
        total = detected_total if detected_total is not None else total
        if not rows:
            break
        all_rows.extend(rows)
        if total is not None and len(all_rows) >= total:
            break
        if len(rows) < int(form_data["pageSize"]):
            break
    if not all_rows:
        raise RuntimeError("猫超商品搜索接口未返回可写入 Excel 的数据")
    master_rows = _build_tmcs_master_rows(all_rows)
    if not master_rows:
        raise RuntimeError("猫超商品搜索接口未返回可同步的商品行")
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "商品列表"
    sheet.append(TMCS_MASTER_HEADERS)
    for row in master_rows:
        sheet.append(row)
    workbook.save(destination)
    return {
        "status_code": 200,
        "export_task_id": None,
        "export_url": None,
        "file_name": destination.name,
        "download_size": destination.stat().st_size,
        "source": "product_search_api_fallback",
        "row_count": len(master_rows),
        "item_count": len(all_rows),
    }


def _download_goods_export(*, export_scene: dict[str, Any], search_scene: dict[str, Any], destination: Path) -> dict[str, Any]:
    form_data = dict(export_scene.get("post_data_form") or {})
    if "_scm_token_" not in form_data or "query" not in form_data:
        raise RuntimeError("商品导出 scene 缺少 _scm_token_ 或 query")
    headers = dict(export_scene.get("headers") or {})
    method = str(export_scene.get("method") or "POST").upper()
    url = str(export_scene.get("url") or "").strip()
    status_code, payload, _ = tmcs_request(method, url, headers=headers, data_body=form_encode(form_data))
    if isinstance(payload, dict) and payload.get("success") is False and payload.get("errorCode") == "PL_GEI_U00001":
        return _download_goods_from_search(search_scene=search_scene, destination=destination)
    task_id = extract_export_task_id(payload)
    download_url = find_download_url(payload)
    file_name = None
    if isinstance(payload, dict):
        data_node = payload.get("data")
        if isinstance(data_node, dict):
            file_name = data_node.get("fileName")
    if task_id:
        gei_url = gei_task_download_url(url, task_id)
        try:
            _, nested_payload, nested_content = tmcs_download(gei_url, headers=headers)
            content, _ = resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=headers)
        except RuntimeError:
            return _download_goods_from_search(search_scene=search_scene, destination=destination)
    elif download_url:
        _, nested_payload, nested_content = tmcs_download(download_url, headers=headers)
        content, _ = resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=headers)
    else:
        raise RuntimeError(f"商品导出接口未返回 taskId 或下载地址：{json.dumps(payload, ensure_ascii=False)[:500]}")
    if not content or not is_probably_excel(content):
        raise RuntimeError("商品导出返回的不是合法 Excel 文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return {
        "status_code": status_code,
        "export_task_id": task_id,
        "export_url": download_url,
        "file_name": file_name,
        "download_size": len(content),
    }


_ITEM_CODE_CANDIDATES = ["商品编码", "itemId"]
_SKU_CODE_CANDIDATES = ["SKU编码", "skuId"]
_ITEM_STATUS_HEADER = "商品上下架状态"
_SKU_STATUS_HEADER = "SKU上下架状态"


def _build_status_index(import_rows: list[list[object]], import_header: list[str]) -> dict[tuple, tuple] | None:
    """以(商品编码, SKU编码)为键，建立本次下载数据的上下架状态查询表。

    缺少商品编码或商品上下架状态列时返回 None（老结构/测试数据），由调用方跳过状态回写。
    """
    item_idx = _optional_column_index(import_header, _ITEM_CODE_CANDIDATES)
    item_status_idx = _optional_column_index(import_header, [_ITEM_STATUS_HEADER])
    if item_idx is None or item_status_idx is None:
        return None
    sku_idx = _optional_column_index(import_header, _SKU_CODE_CANDIDATES)
    sku_status_idx = _optional_column_index(import_header, [_SKU_STATUS_HEADER])
    index: dict[tuple, tuple] = {}
    for row in import_rows:
        key = (_norm(_cell(row, item_idx)), _norm(_cell(row, sku_idx)))
        index[key] = (_cell(row, item_status_idx), _cell(row, sku_status_idx))
    return index


def _sync_shelf_status(
    *,
    latest_header: list[str],
    latest_rows: list[list[object]],
    import_header: list[str],
    import_rows: list[list[object]],
) -> dict[str, int]:
    """用本次下载的最新数据回写存量行的上下架状态（新增行已携带最新状态，无需处理）。"""
    stats = {"status_updated": 0, "status_unchanged": 0, "status_not_in_import": 0}
    status_index = _build_status_index(import_rows, import_header)
    item_idx = _optional_column_index(latest_header, _ITEM_CODE_CANDIDATES)
    item_status_idx = _optional_column_index(latest_header, [_ITEM_STATUS_HEADER])
    if status_index is None or item_idx is None or item_status_idx is None:
        return stats  # 老结构/测试数据无状态列，跳过状态回写
    sku_idx = _optional_column_index(latest_header, _SKU_CODE_CANDIDATES)
    sku_status_idx = _optional_column_index(latest_header, [_SKU_STATUS_HEADER])
    for row in latest_rows:
        key = (_norm(_cell(row, item_idx)), _norm(_cell(row, sku_idx)))
        fresh = status_index.get(key)
        if fresh is None:
            stats["status_not_in_import"] += 1
            continue
        item_status, sku_status = fresh
        changed = False
        if item_status not in (None, "") and item_status_idx < len(row) and row[item_status_idx] != item_status:
            row[item_status_idx] = item_status
            changed = True
        if (
            sku_status not in (None, "")
            and sku_status_idx is not None
            and sku_status_idx < len(row)
            and row[sku_status_idx] != sku_status
        ):
            row[sku_status_idx] = sku_status
            changed = True
        stats["status_updated" if changed else "status_unchanged"] += 1
    return stats


def _sync_product_workbook(*, latest_path: Path, import_path: Path, jst_path: Path, dry_run: bool) -> dict[str, Any]:
    latest_header, latest_rows = _load_sheet_data(latest_path)
    import_header, import_rows = _load_sheet_data(import_path)
    jst_header, jst_rows = _load_sheet_data(jst_path)

    _require_columns(jst_header, ["商品编码"], jst_path.name)

    goods_candidates = ["货品编码", "erpCode", "itemId", "storageGoodsId"]
    barcode_candidates = ["条码", "barcode", "barCode"]
    latest_goods_idx = _column_index(latest_header, goods_candidates, latest_path.name)
    latest_barcode_idx = _column_index(latest_header, barcode_candidates, latest_path.name)
    import_goods_idx = _column_index(import_header, goods_candidates, import_path.name)

    latest_goods_codes = {
        _norm(row[latest_goods_idx] if latest_goods_idx < len(row) else None)
        for row in latest_rows
        if _norm(row[latest_goods_idx] if latest_goods_idx < len(row) else None)
    }

    appended_rows: list[list[object]] = []
    for row in import_rows:
        goods_code = _norm(row[import_goods_idx] if import_goods_idx < len(row) else None)
        if not goods_code or goods_code in latest_goods_codes:
            continue
        latest_goods_codes.add(goods_code)
        appended_rows.append(_build_row_by_latest_header(latest_header, import_header, row))

    exact_map, normalized_codes = _build_jst_code_pool(jst_rows, jst_header)
    stats = {
        "exact_replaced": 0,
        "fuzzy_replaced": 0,
        "unchanged": 0,
        "empty_barcode": 0,
        "missed": 0,
        "multiple_candidates": 0,
    }

    for row in appended_rows:
        current_barcode = row[latest_barcode_idx] if latest_barcode_idx < len(row) else None
        replacement, match_type = _find_replacement_code(current_barcode, exact_map, normalized_codes)
        if match_type == "empty":
            stats["empty_barcode"] += 1
            continue
        if match_type == "multiple":
            stats["multiple_candidates"] += 1
            continue
        if match_type == "miss" or replacement is None:
            stats["missed"] += 1
            continue
        if _norm(current_barcode) == replacement:
            stats["unchanged"] += 1
            continue
        row[latest_barcode_idx] = replacement
        if match_type == "exact":
            stats["exact_replaced"] += 1
        else:
            stats["fuzzy_replaced"] += 1

    status_stats = _sync_shelf_status(
        latest_header=latest_header,
        latest_rows=latest_rows,
        import_header=import_header,
        import_rows=import_rows,
    )

    if not dry_run:
        _save_latest_rows(latest_path, latest_rows + appended_rows)

    return {
        "original_latest_rows": len(latest_rows),
        "import_rows": len(import_rows),
        "new_rows": len(appended_rows),
        "final_latest_rows": len(latest_rows) + (0 if dry_run else len(appended_rows)),
        **stats,
        **status_stats,
    }


def learn_product_sync(*, force: bool = False) -> CommandResponse:
    inputs = {"site": TMCS_SITE, "scenes": [TMCS_PRODUCT_SEARCH_SCENE, TMCS_PRODUCT_EXPORT_SCENE], "force": force}
    try:
        search_scene, search_check, search_path = ensure_scene_assets(
            site=TMCS_SITE,
            scene=TMCS_PRODUCT_SEARCH_SCENE,
            force=force,
            next_command="ops tmcs auth capture",
        )
    except RuntimeError:
        search_path = Path(get_config().sessionhub_root).expanduser() / "data" / "sessions" / TMCS_SITE / f"{TMCS_PRODUCT_SEARCH_SCENE}.json"
        search_scene = read_json(search_path)
        search_check = {"status": search_scene.get("status", "unknown"), "fallback": "loaded_existing_scene"}
    try:
        export_scene, export_check, export_path = ensure_scene_assets(
            site=TMCS_SITE,
            scene=TMCS_PRODUCT_EXPORT_SCENE,
            force=force,
            next_command="ops tmcs auth capture",
        )
    except RuntimeError:
        export_path = Path(get_config().sessionhub_root).expanduser() / "data" / "sessions" / TMCS_SITE / f"{TMCS_PRODUCT_EXPORT_SCENE}.json"
        export_scene = read_json(export_path)
        export_check = {"status": export_scene.get("status", "unknown"), "fallback": "loaded_existing_scene"}
    template_path = _write_template(export_scene=export_scene, search_scene=search_scene)
    context_path = write_runtime_context(
        task_name="tmcs_product_learn",
        status="success",
        inputs=inputs,
        outputs={
            "template_path": str(template_path),
            "search_scene_path": str(search_path),
            "export_scene_path": str(export_path),
            "search_check": search_check,
            "export_check": export_check,
        },
        artifacts=[str(search_path), str(export_path), str(template_path)],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="product learn",
        data={
            "site": TMCS_SITE,
            "search_scene": TMCS_PRODUCT_SEARCH_SCENE,
            "export_scene": TMCS_PRODUCT_EXPORT_SCENE,
            "template_path": str(template_path),
            "context_path": str(context_path),
            "next_command": "ops --json tmcs product sync",
        },
    )


def run_product_sync(
    *,
    dry_run: bool = False,
    use_local_only: bool = False,
    force_refresh: bool = False,
) -> CommandResponse:
    retried_for_auth = False
    auth_refresh_applied = False
    while True:
        template = _load_template()
        defaults = template.get("defaults") or {}
        import_path = Path(str(defaults.get("import_path") or get_config().tmcs_product_import_path)).expanduser()
        latest_path = Path(str(defaults.get("latest_path") or get_config().tmcs_product_latest_path)).expanduser()
        jst_path = Path(str(defaults.get("jst_path") or get_config().jst_product_source_path)).expanduser()

        search_scene_data = template.get("search") or {}
        export_scene_data = template.get("export") or load_scene_or_fail(TMCS_SITE, TMCS_PRODUCT_EXPORT_SCENE, next_command="ops tmcs product learn")
        if not use_local_only:
            check_scene_or_fail(TMCS_SITE, TMCS_PRODUCT_SEARCH_SCENE, next_command="ops tmcs product learn")

        should_auto_download = (not use_local_only) and (force_refresh or not import_path.exists())
        if should_auto_download and not dry_run:
            try:
                download_meta = {
                    "used_backend_export": True,
                    "downloaded": True,
                    **_download_goods_export(export_scene=export_scene_data, search_scene=search_scene_data, destination=import_path),
                }
            except RuntimeError as exc:
                if not retried_for_auth and is_probable_auth_error(exc):
                    require_interactive_recovery(TMCS_PRODUCT_EXPORT_SCENE)
                    learn_product_sync(force=True)
                    mark_scene_refreshed(TMCS_PRODUCT_EXPORT_SCENE)
                    retried_for_auth = True
                    auth_refresh_applied = True
                    continue
                raise
        else:
            download_meta = {
                "used_backend_export": should_auto_download,
                "downloaded": False,
                "auto_download_reason": (
                    "use_local_only"
                    if use_local_only
                    else "existing_import_file"
                    if import_path.exists() and not force_refresh
                    else "dry_run"
                    if dry_run
                    else "not_needed"
                ),
            }
        break

    if auth_refresh_applied:
        download_meta["auth_refresh_applied"] = True

    if not import_path.exists():
        raise RuntimeError(f"未找到猫超导入文件：{import_path}")
    if not jst_path.exists():
        raise RuntimeError(f"未找到聚水潭商品资料：{jst_path}")

    effective_latest_path = latest_path
    if not latest_path.exists():
        if dry_run:
            effective_latest_path = import_path
        else:
            latest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(import_path, latest_path)

    sync_summary = _sync_product_workbook(
        latest_path=effective_latest_path,
        import_path=import_path,
        jst_path=jst_path,
        dry_run=dry_run,
    )
    context_path = write_runtime_context(
        task_name="tmcs_product_sync_run",
        status="success",
        inputs={
            "dry_run": dry_run,
            "use_local_only": use_local_only,
            "force_refresh": force_refresh,
            "import_path": str(import_path),
            "latest_path": str(latest_path),
            "jst_path": str(jst_path),
        },
        outputs={**download_meta, **sync_summary, "output_path": str(latest_path)},
        artifacts=[str(import_path), str(latest_path)],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="product sync",
        data={
            "source": str(import_path),
            "output_path": str(latest_path),
            "jst_file": str(jst_path),
            "scene": TMCS_PRODUCT_EXPORT_SCENE,
            "search_scene": TMCS_PRODUCT_SEARCH_SCENE,
            "context_path": str(context_path),
            "dry_run": dry_run,
            **download_meta,
            **sync_summary,
        },
    )


def list_products() -> CommandResponse:
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="product list",
        data={"next_command": "ops --json tmcs product sync", "mode": "cli"},
    )
