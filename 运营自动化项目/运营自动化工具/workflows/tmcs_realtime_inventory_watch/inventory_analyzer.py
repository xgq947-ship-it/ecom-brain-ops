"""猫超库存实时监测 — 业务计算（关联、剩余库存、风险判定）。

纯业务逻辑，输入是 excel_loader 读出来的原始行，输出标准化行 / 中间表 / 风险项。
不碰平台、不碰文件路径解析（路径由 steps 决定）。所有数值清洗经 excel_loader.clean_number。
"""

from __future__ import annotations

from typing import Any

from workflows.tmcs_realtime_inventory_watch import excel_loader as xl


def _norm(text: Any) -> str:
    return "" if text is None else str(text).strip()


# ── 表3 猫超商品列表 ─────────────────────────────────────────────────────────
def load_maochao_goods(headers: list[str], records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """筛选「商品上下架状态=上架」，提取 SKU编码 / 条码。条码去重记 warning。"""
    sku_col = xl.resolve_field(headers, xl.GOODS_SKU_CODE, label="SKU编码")
    barcode_col = xl.resolve_field(headers, xl.GOODS_BARCODE, label="条码")
    status_col = xl.resolve_field(headers, xl.GOODS_LISTING_STATUS, label="商品上下架状态")
    try:
        name_col: str | None = xl.resolve_field(headers, xl.GOODS_NAME, label="商品名称")
    except xl.FieldNotFoundError:
        name_col = None  # 商品名称为可选列，缺失则留空

    warnings: list[str] = []
    seen_barcodes: set[str] = set()
    rows: list[dict[str, Any]] = []
    for record in records:
        if _norm(record.get(status_col)) != "上架":
            continue
        sku_code = xl.norm_id(record.get(sku_col))
        barcode = xl.norm_id(record.get(barcode_col))
        if not sku_code or not barcode:
            continue
        if barcode in seen_barcodes:
            warnings.append(f"猫超商品列表条码重复：{barcode}（SKU {sku_code}）")
        else:
            seen_barcodes.add(barcode)
        rows.append(
            {
                "sku_code": sku_code,
                "barcode": barcode,
                "product_name": _norm(record.get(name_col)) if name_col else "",
            }
        )
    return rows, warnings


# ── 表1 聚水潭商品资料 ───────────────────────────────────────────────────────
def load_jst_products(
    headers: list[str], records: list[dict[str, Any]], *, brands: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """筛选指定品牌，提取 商品编码/实际库存/订单占有，计算剩余库存。"""
    code_col = xl.resolve_field(headers, xl.JST_PRODUCT_CODE, label="商品编码")
    actual_col = xl.resolve_field(headers, xl.JST_ACTUAL_STOCK, label="实际库存")
    hold_col = xl.resolve_field(headers, xl.JST_ORDER_HOLD, label="订单占有")
    brand_col = xl.resolve_field(headers, xl.JST_BRAND, label="品牌")

    brand_set = {b.strip() for b in brands if b.strip()}
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for record in records:
        brand = _norm(record.get(brand_col))
        if brand_set and brand not in brand_set:
            continue
        product_code = xl.norm_id(record.get(code_col))
        if not product_code:
            continue
        actual, actual_empty = xl.clean_number(record.get(actual_col))
        hold, hold_empty = xl.clean_number(record.get(hold_col))
        if actual_empty:
            warnings.append(f"聚水潭商品 {product_code} 实际库存为空，按 0 处理")
        if hold_empty:
            warnings.append(f"聚水潭商品 {product_code} 订单占有为空，按 0 处理")
        rows.append(
            {
                "product_code": product_code,
                "brand": brand,
                "actual_stock": actual,
                "order_hold": hold,
                "remaining_stock": actual - hold,  # 可为负
            }
        )
    return rows, warnings


# ── 表2 猫超库存明细 ─────────────────────────────────────────────────────────
def load_tmcs_stock(
    headers: list[str], records: list[dict[str, Any]], *, warehouse_code: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """按商家仓 code 筛选（表无该列则不筛），提取平台SKUID/专享/共享可售量。"""
    sku_col = xl.resolve_field(headers, xl.TMCS_PLATFORM_SKU_ID, label="平台SKUID")
    dedicated_col = xl.resolve_field(headers, xl.TMCS_DEDICATED_SELLABLE, label="专享现货库存可售量")
    shared_col = xl.resolve_field(headers, xl.TMCS_SHARED_SELLABLE, label="共享现货库存可售量")
    warehouse_col = None
    try:
        warehouse_col = xl.resolve_field(headers, xl.TMCS_WAREHOUSE_CODE, label="商家仓code")
    except xl.FieldNotFoundError:
        warehouse_col = None  # 导出表若已按仓筛选则可能无此列

    warnings: list[str] = []
    if warehouse_col is None:
        warnings.append("猫超库存明细缺少商家仓code列，跳过仓库筛选，按全表处理")
    rows: list[dict[str, Any]] = []
    for record in records:
        if warehouse_col is not None and warehouse_code:
            if _norm(record.get(warehouse_col)) != warehouse_code:
                continue
        platform_sku_id = xl.norm_id(record.get(sku_col))
        if not platform_sku_id:
            continue
        dedicated, ded_empty = xl.clean_number(record.get(dedicated_col))
        shared, shr_empty = xl.clean_number(record.get(shared_col))
        if ded_empty:
            warnings.append(f"猫超SKU {platform_sku_id} 专享可售库存为空，按 0 处理")
        if shr_empty:
            warnings.append(f"猫超SKU {platform_sku_id} 共享可售库存为空，按 0 处理")
        rows.append(
            {
                "platform_sku_id": platform_sku_id,
                "dedicated_sellable_stock": dedicated,
                "shared_sellable_stock": shared,
                "tmcs_total_sellable_stock": dedicated + shared,
            }
        )
    return rows, warnings


# ── 表4 中间表：表1.商品编码 = 表3.条码 ──────────────────────────────────────
def build_inventory_table(
    jst_rows: list[dict[str, Any]], goods_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """关联聚水潭(商品编码)与猫超商品列表(条码)，输出含 SKU编码 + 剩余库存 的中间表。

    SKU编码重复时优先保留第一条并记 duplicate warning。
    """
    jst_by_code: dict[str, dict[str, Any]] = {}
    for row in jst_rows:
        jst_by_code.setdefault(row["product_code"], row)

    warnings: list[str] = []
    seen_sku: set[str] = set()
    table: list[dict[str, Any]] = []
    for goods in goods_rows:
        barcode = goods["barcode"]
        jst = jst_by_code.get(barcode)
        if jst is None:
            continue
        sku_code = goods["sku_code"]
        if sku_code in seen_sku:
            warnings.append(f"中间表 SKU 编码重复，保留首条：{sku_code}")
            continue
        seen_sku.add(sku_code)
        table.append(
            {
                "sku_code": sku_code,
                "barcode": barcode,
                "product_name": goods.get("product_name", ""),
                "product_code": jst["product_code"],
                "actual_stock": jst["actual_stock"],
                "order_hold": jst["order_hold"],
                "remaining_stock": jst["remaining_stock"],
            }
        )
    return table, warnings


# ── 风险判定：表4.SKU编码 = 表2.平台SKUID ────────────────────────────────────
def detect_inventory_risks(
    inventory_table: list[dict[str, Any]], tmcs_rows: list[dict[str, Any]], *, threshold: float
) -> tuple[list[dict[str, Any]], int]:
    """记录上架 SKU 中「聚水潭实际库存 < threshold」的项，并附猫超可售库存以供比对。

    口径（按用户确认）：
    - 门槛用聚水潭实际库存（实际库存数），不是剩余库存。
    - 不要求猫超可售 > 实际库存，单边为 0 仍保留（聚水潭=0猫超>0 或反之）。
    - 但「聚水潭实际库存=0 且 猫超可售=0」两边都无货，无意义，剔除。
    - 仅对能在猫超库存明细关联到的 SKU 输出（这样才有「猫超实际库存」可填）。

    返回 (记录列表, 实际库存<threshold 的 SKU 数)。
    """
    tmcs_by_sku: dict[str, dict[str, Any]] = {}
    for row in tmcs_rows:
        tmcs_by_sku.setdefault(row["platform_sku_id"], row)

    risks: list[dict[str, Any]] = []
    low_stock_count = 0
    for item in inventory_table:
        actual = item["actual_stock"]
        if actual >= threshold:
            continue
        low_stock_count += 1
        tmcs = tmcs_by_sku.get(item["sku_code"])
        if tmcs is None:
            continue
        tmcs_total = tmcs["tmcs_total_sellable_stock"]
        if actual == 0 and tmcs_total == 0:
            continue
        risks.append(
            {
                "sku_code": item["sku_code"],
                "barcode": item["barcode"],
                "product_name": item.get("product_name", ""),
                "product_code": item["product_code"],
                "actual_stock": actual,
                "order_hold": item["order_hold"],
                "remaining_stock": item["remaining_stock"],
                "tmcs_total_sellable_stock": tmcs_total,
                "dedicated_sellable_stock": tmcs["dedicated_sellable_stock"],
                "shared_sellable_stock": tmcs["shared_sellable_stock"],
            }
        )
    return risks, low_stock_count


def detect_low_tmcs_stock(
    inventory_table: list[dict[str, Any]],
    tmcs_rows: list[dict[str, Any]],
    *,
    jst_threshold: float,
    tmcs_threshold: float,
) -> list[dict[str, Any]]:
    """子表：排除风险表后，记录「聚水潭实际库存 >= jst_threshold 且 猫超可售 < tmcs_threshold」的 SKU。

    - 聚水潭实际库存 >= jst_threshold：自然排除风险表（风险表是聚水潭实际库存 < jst_threshold）。
    - 猫超可售(专享+共享) < tmcs_threshold：猫超侧库存偏低、需补货。
    - 字段与风险表一致。
    """
    tmcs_by_sku: dict[str, dict[str, Any]] = {}
    for row in tmcs_rows:
        tmcs_by_sku.setdefault(row["platform_sku_id"], row)

    records: list[dict[str, Any]] = []
    for item in inventory_table:
        if item["actual_stock"] < jst_threshold:
            continue
        tmcs = tmcs_by_sku.get(item["sku_code"])
        if tmcs is None:
            continue
        tmcs_total = tmcs["tmcs_total_sellable_stock"]
        if tmcs_total >= tmcs_threshold:
            continue
        records.append(
            {
                "sku_code": item["sku_code"],
                "barcode": item["barcode"],
                "product_name": item.get("product_name", ""),
                "product_code": item["product_code"],
                "actual_stock": item["actual_stock"],
                "order_hold": item["order_hold"],
                "remaining_stock": item["remaining_stock"],
                "tmcs_total_sellable_stock": tmcs_total,
                "dedicated_sellable_stock": tmcs["dedicated_sellable_stock"],
                "shared_sellable_stock": tmcs["shared_sellable_stock"],
            }
        )
    return records
