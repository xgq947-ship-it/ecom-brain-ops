from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook, load_workbook


TMCS_REQUIRED_HEADERS = ("商品编码", "SKU编码", "条码")
JST_REQUIRED_HEADERS = ("商品编码", "淘系控价", "成本价")
ROI_CONFIG_REQUIRED_KEYS = (
    "supply_price_factor",
    "vip_discount_rate",
    "general_fee_rate",
    "other_fee_rate",
    "storage_fee_rate",
    "tax_rate",
    "management_fee_rate",
    "refund_rate",
    "refund_flat_fee",
    "domestic_shipping_fee",
    "gift_cost",
    "safe_profit_rate",
    "ideal_promotion_ratio",
)


def _normalize_header(value) -> str:
    return str(value or "").strip()


def _load_sheet(path: Path):
    workbook = load_workbook(path, read_only=True, data_only=False)
    return workbook, workbook[workbook.sheetnames[0]]


def _header_map(sheet) -> dict[str, int]:
    headers = {}
    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    for column, raw_value in enumerate(header_row, start=1):
        header = _normalize_header(raw_value)
        if header:
            headers[header] = column
    return headers


def _require_headers(headers: dict[str, int], required: tuple[str, ...], file_label: str) -> None:
    missing = [name for name in required if name not in headers]
    if missing:
        raise ValueError(f"{file_label} 缺少字段：{', '.join(missing)}")


def find_tmcs_barcode(path: Path, *, sku_code: str | None = None, product_code: str | None = None) -> dict[str, str]:
    workbook, sheet = _load_sheet(path)
    try:
        headers = _header_map(sheet)
        _require_headers(headers, TMCS_REQUIRED_HEADERS, "猫超商品列表")
        product_index = headers["商品编码"] - 1
        sku_index = headers["SKU编码"] - 1
        barcode_index = headers["条码"] - 1
        if sku_code:
            mode = "sku_code"
            lookup_value = sku_code
        elif product_code:
            mode = "product_code"
            lookup_value = product_code
        else:
            raise ValueError("缺少猫超查询键：sku_code 或 product_code")

        first_match_barcode = None
        for row_values in sheet.iter_rows(min_row=2, values_only=True):
            if mode == "sku_code":
                value = _normalize_header(row_values[sku_index] if sku_index < len(row_values) else None)
            else:
                value = _normalize_header(row_values[product_index] if product_index < len(row_values) else None)
            if value != lookup_value:
                continue
            barcode = _normalize_header(row_values[barcode_index] if barcode_index < len(row_values) else None)
            if not barcode:
                raise ValueError(f"{'SKU' if mode == 'sku_code' else '商品编码'} {lookup_value} 已找到，但条码为空")
            if mode == "sku_code":
                return {"sku_code": lookup_value, "barcode": barcode}
            if first_match_barcode is None:
                first_match_barcode = barcode
        if first_match_barcode is not None:
            return {"product_code": lookup_value, "barcode": first_match_barcode}
    finally:
        workbook.close()
    if sku_code:
        raise ValueError(f"猫超商品列表未找到 SKU 编码：{sku_code}")
    raise ValueError(f"猫超商品列表未找到 商品编码：{product_code}")


def _parse_control_price(raw_value, *, multi_price_strategy: str = "error") -> float:
    """解析聚水潭「淘系控价」。

    multi_price_strategy：
    - "error"（默认）：单格多值（如 268/298）直接报错，保持 tmcs_sku_roi 原有行为。
    - "max"：单格多值时取最高价，用于全站推 ROI 链路（按最高售价测算更稳健）。
    """
    text = str(raw_value or "").strip()
    if not text:
        raise ValueError("聚水潭商品资料中的淘系控价为空")
    # 单元格内换行：真实 \n / \r，以及 openpyxl 未解码的 OOXML 转义 _x000A_/_x000D_，统一当分隔符。
    normalized = (
        text.replace("_x000A_", " ")
        .replace("_x000D_", " ")
        .replace("\n", " ")
        .replace("\r", " ")
    )
    parts = [part for part in normalized.split() if part]
    try:
        values = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"聚水潭商品资料中的淘系控价不是有效数字：{text}") from exc
    if len(values) > 1:
        if multi_price_strategy == "max":
            return max(values)
        raise ValueError(f"聚水潭商品资料中的淘系控价存在多个值：{text}")
    return values[0]


def find_jst_product(
    path: Path,
    barcode_as_product_code: str,
    *,
    multi_price_strategy: str = "error",
) -> dict[str, float | str]:
    workbook, sheet = _load_sheet(path)
    try:
        headers = _header_map(sheet)
        _require_headers(headers, JST_REQUIRED_HEADERS, "聚水潭商品资料")
        code_index = headers["商品编码"] - 1
        price_index = headers["淘系控价"] - 1
        cost_index = headers["成本价"] - 1
        matches = []
        for row_values in sheet.iter_rows(min_row=2, values_only=True):
            code = _normalize_header(row_values[code_index] if code_index < len(row_values) else None)
            if code != barcode_as_product_code:
                continue
            raw_cost = row_values[cost_index] if cost_index < len(row_values) else None
            matches.append(
                {
                    "product_code": code,
                    "price": _parse_control_price(
                        row_values[price_index] if price_index < len(row_values) else None,
                        multi_price_strategy=multi_price_strategy,
                    ),
                    "cost": float(raw_cost),
                }
            )
    finally:
        workbook.close()

    if not matches:
        raise ValueError(f"聚水潭商品资料未找到商品编码：{barcode_as_product_code}")
    if len(matches) > 1:
        raise ValueError(f"聚水潭商品资料中商品编码重复：{barcode_as_product_code}")
    match = matches[0]
    if match["cost"] <= 0:
        raise ValueError(f"聚水潭商品资料中的成本价无效：{match['cost']}")
    return match


def load_roi_config(path: Path) -> dict[str, float]:
    if not path.exists():
        raise ValueError(f"ROI 配置文件不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"ROI 配置文件不是合法 JSON：{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"ROI 配置文件格式错误，顶层必须是对象：{path}")

    missing = [key for key in ROI_CONFIG_REQUIRED_KEYS if key not in payload]
    if missing:
        raise ValueError(f"ROI 配置文件缺少字段：{', '.join(missing)}")

    config: dict[str, float] = {}
    for key in ROI_CONFIG_REQUIRED_KEYS:
        raw_value = payload[key]
        if not isinstance(raw_value, (int, float)):
            raise ValueError(f"ROI 配置字段必须是数字：{key}={raw_value}")
        value = float(raw_value)
        if key in {"supply_price_factor", "safe_profit_rate", "ideal_promotion_ratio"} and value <= 0:
            raise ValueError(f"ROI 配置字段必须大于 0：{key}={raw_value}")
        if key not in {"supply_price_factor", "safe_profit_rate", "ideal_promotion_ratio"} and value < 0:
            raise ValueError(f"ROI 配置字段不能小于 0：{key}={raw_value}")
        config[key] = value
    return config


def write_result_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_result_excel(path: Path, payload: dict) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ROI结果"
    sheet.append(["字段", "值"])
    for key in ("保本ROI", "安全ROI", "理想ROI"):
        sheet.append([key, payload[key]])
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    workbook.close()
    return path
