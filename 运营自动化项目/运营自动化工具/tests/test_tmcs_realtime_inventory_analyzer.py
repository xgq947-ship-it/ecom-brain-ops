"""inventory_analyzer 业务计算测试：筛选 / 关联 / 剩余库存 / 风险判定。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.tmcs_realtime_inventory_watch import inventory_analyzer as ia
from workflows.tmcs_realtime_inventory_watch import excel_loader as xl


# ── 表3 猫超商品列表 ─────────────────────────────────────────────────────────
def test_load_maochao_goods_filters_active_and_dedupes_barcode():
    headers = ["SKU编码", "条码", "商品上下架状态"]
    records = [
        {"SKU编码": "S1", "条码": "B1", "商品上下架状态": "上架"},
        {"SKU编码": "S2", "条码": "B2", "商品上下架状态": "下架"},
        {"SKU编码": "S3", "条码": "B1", "商品上下架状态": "上架"},  # 条码重复
    ]
    rows, warnings = ia.load_maochao_goods(headers, records)
    assert {r["sku_code"] for r in rows} == {"S1", "S3"}  # 下架被过滤
    assert any("条码重复" in w for w in warnings)


# ── 表1 聚水潭商品资料 ───────────────────────────────────────────────────────
def test_product_name_flows_from_goods_to_risk():
    goods_headers = ["SKU编码", "条码", "商品上下架状态", "商品名称"]
    goods_records = [{"SKU编码": "S1", "条码": "B1", "商品上下架状态": "上架", "商品名称": "奥克斯腰部按摩器"}]
    goods, _ = ia.load_maochao_goods(goods_headers, goods_records)
    assert goods[0]["product_name"] == "奥克斯腰部按摩器"
    jst_rows = [{"product_code": "B1", "actual_stock": 5, "order_hold": 0, "remaining_stock": 5, "brand": "奥克斯"}]
    table, _ = ia.build_inventory_table(jst_rows, goods)
    assert table[0]["product_name"] == "奥克斯腰部按摩器"
    risks, _ = ia.detect_inventory_risks(table, _tmcs("S1", 3, 2), threshold=20)
    assert risks[0]["product_name"] == "奥克斯腰部按摩器"


def test_load_maochao_goods_name_optional():
    # 缺商品名称列不报错，product_name 留空
    headers = ["SKU编码", "条码", "商品上下架状态"]
    rows, _ = ia.load_maochao_goods(headers, [{"SKU编码": "S1", "条码": "B1", "商品上下架状态": "上架"}])
    assert rows[0]["product_name"] == ""


def test_load_jst_products_filters_brand_and_computes_remaining():
    headers = ["商品编码", "实际库存数", "订单占有数", "品牌"]
    records = [
        {"商品编码": "B1", "实际库存数": 30, "订单占有数": 5, "品牌": "苏泊尔"},
        {"商品编码": "B2", "实际库存数": 10, "订单占有数": 12, "品牌": "奥克斯"},
        {"商品编码": "B3", "实际库存数": 99, "订单占有数": 0, "品牌": "美的"},  # 非目标品牌
    ]
    rows, _ = ia.load_jst_products(headers, records, brands=["苏泊尔", "奥克斯"])
    by_code = {r["product_code"]: r for r in rows}
    assert set(by_code) == {"B1", "B2"}
    assert by_code["B1"]["remaining_stock"] == 25
    assert by_code["B2"]["remaining_stock"] == -2  # 可为负


def test_load_jst_products_empty_value_warns():
    headers = ["商品编码", "实际库存数", "订单占有数", "品牌"]
    records = [{"商品编码": "B1", "实际库存数": None, "订单占有数": "", "品牌": "苏泊尔"}]
    rows, warnings = ia.load_jst_products(headers, records, brands=["苏泊尔"])
    assert rows[0]["remaining_stock"] == 0
    assert len(warnings) == 2


def test_missing_field_raises_clear_error():
    headers = ["商品编码", "品牌"]  # 缺实际库存
    with pytest.raises(xl.FieldNotFoundError):
        ia.load_jst_products(headers, [], brands=["苏泊尔"])


# ── 表2 猫超库存明细 ─────────────────────────────────────────────────────────
def test_load_tmcs_stock_filters_warehouse_and_sums():
    headers = ["平台SKUID", "专享现货库存可售量", "共享现货库存可售量", "商家仓code"]
    records = [
        {"平台SKUID": "S1", "专享现货库存可售量": 10, "共享现货库存可售量": 5, "商家仓code": "mc_aokesi_suolong"},
        {"平台SKUID": "S2", "专享现货库存可售量": 3, "共享现货库存可售量": 2, "商家仓code": "other_wh"},
    ]
    rows, _ = ia.load_tmcs_stock(headers, records, warehouse_code="mc_aokesi_suolong")
    assert len(rows) == 1
    assert rows[0]["platform_sku_id"] == "S1"
    assert rows[0]["tmcs_total_sellable_stock"] == 15


# ── 表4 中间表 ───────────────────────────────────────────────────────────────
def test_build_inventory_table_joins_code_to_barcode():
    jst_rows = [
        {"product_code": "B1", "actual_stock": 30, "order_hold": 5, "remaining_stock": 25, "brand": "苏泊尔"},
    ]
    goods_rows = [{"sku_code": "S1", "barcode": "B1"}, {"sku_code": "S9", "barcode": "BX"}]
    table, _ = ia.build_inventory_table(jst_rows, goods_rows)
    assert len(table) == 1
    assert table[0]["sku_code"] == "S1"
    assert table[0]["remaining_stock"] == 25


def test_numeric_id_float_normalized_for_join():
    # Excel 把 13 位 ID 存成 float：表3.SKU编码=5265333653202(str)，表2.平台skuId=5265333653202.0(float)
    goods_headers = ["SKU编码", "条码", "商品上下架状态"]
    goods_records = [{"SKU编码": "5265333653202", "条码": 762065566026.0, "商品上下架状态": "上架"}]
    goods, _ = ia.load_maochao_goods(goods_headers, goods_records)
    assert goods[0]["sku_code"] == "5265333653202"
    assert goods[0]["barcode"] == "762065566026"  # float 条码也还原为整数串

    jst_headers = ["商品编码", "实际库存数", "订单占有数", "品牌"]
    jst_records = [{"商品编码": 762065566026.0, "实际库存数": 3, "订单占有数": 0, "品牌": "奥克斯"}]
    jst, _ = ia.load_jst_products(jst_headers, jst_records, brands=["奥克斯"])
    assert jst[0]["product_code"] == "762065566026"

    table, _ = ia.build_inventory_table(jst, goods)
    assert len(table) == 1  # 表1.商品编码(float) = 表3.条码(str) 能关联

    tmcs_headers = ["平台SKUID", "专享现货库存可售量", "共享现货库存可售量", "商家仓code"]
    tmcs_records = [{"平台SKUID": 5265333653202.0, "专享现货库存可售量": 10, "共享现货库存可售量": 0,
                     "商家仓code": "mc_aokesi_suolong"}]
    tmcs, _ = ia.load_tmcs_stock(tmcs_headers, tmcs_records, warehouse_code="mc_aokesi_suolong")
    risks, _ = ia.detect_inventory_risks(table, tmcs, threshold=20)
    assert len(risks) == 1  # 表4.SKU编码(str) = 表2.平台SKUID(float) 能关联出风险


def test_build_inventory_table_dedup_sku_keeps_first():
    jst_rows = [
        {"product_code": "B1", "actual_stock": 1, "order_hold": 0, "remaining_stock": 1, "brand": "苏泊尔"},
        {"product_code": "B2", "actual_stock": 9, "order_hold": 0, "remaining_stock": 9, "brand": "苏泊尔"},
    ]
    goods_rows = [
        {"sku_code": "S1", "barcode": "B1"},
        {"sku_code": "S1", "barcode": "B2"},  # SKU 重复
    ]
    table, warnings = ia.build_inventory_table(jst_rows, goods_rows)
    assert len(table) == 1
    assert table[0]["remaining_stock"] == 1  # 保留首条
    assert any("SKU 编码重复" in w for w in warnings)


# ── 风险判定 ──────────────────────────────────────────────────────────────────
def _table(sku, remaining):
    return [{"sku_code": sku, "barcode": "B", "product_code": "P", "actual_stock": remaining,
             "order_hold": 0, "remaining_stock": remaining}]


def _tmcs(sku, dedicated, shared):
    return [{"platform_sku_id": sku, "dedicated_sellable_stock": dedicated,
             "shared_sellable_stock": shared, "tmcs_total_sellable_stock": dedicated + shared}]


def test_record_when_actual_stock_below_threshold():
    # _table(sku, actual) -> actual_stock=actual；门槛按聚水潭实际库存
    risks, low = ia.detect_inventory_risks(_table("S1", 5), _tmcs("S1", 8, 2), threshold=20)
    assert low == 1
    assert len(risks) == 1
    assert risks[0]["actual_stock"] == 5
    assert risks[0]["tmcs_total_sellable_stock"] == 10


def test_no_record_when_actual_ge_threshold():
    risks, low = ia.detect_inventory_risks(_table("S1", 25), _tmcs("S1", 99, 0), threshold=20)
    assert low == 0
    assert risks == []


def test_record_kept_when_only_tmcs_zero():
    # 单边为 0（聚水潭实际 10、猫超可售 0）仍保留
    risks, low = ia.detect_inventory_risks(_table("S1", 10), _tmcs("S1", 0, 0), threshold=20)
    assert low == 1
    assert len(risks) == 1
    assert risks[0]["tmcs_total_sellable_stock"] == 0


def test_record_kept_when_only_jst_zero():
    # 单边为 0（聚水潭实际 0、猫超可售 5）仍保留（超卖风险）
    risks, _ = ia.detect_inventory_risks(_table("S1", 0), _tmcs("S1", 3, 2), threshold=20)
    assert len(risks) == 1
    assert risks[0]["actual_stock"] == 0
    assert risks[0]["tmcs_total_sellable_stock"] == 5


def test_record_dropped_when_both_zero():
    # 聚水潭实际=0 且 猫超可售=0：两边都无货，剔除
    risks, low = ia.detect_inventory_risks(_table("S1", 0), _tmcs("S1", 0, 0), threshold=20)
    assert low == 1  # 仍计入低库存计数
    assert risks == []


def test_no_record_when_no_tmcs_match():
    # 无猫超库存数据则无「猫超实际库存」可比对，不输出
    risks, low = ia.detect_inventory_risks(_table("S1", 10), _tmcs("OTHER", 5, 5), threshold=20)
    assert low == 1
    assert risks == []


# ── 子表：猫超低库存（聚水潭>=20 且 猫超可售<50）────────────────────────────
def test_low_tmcs_records_when_jst_ok_and_tmcs_low():
    recs = ia.detect_low_tmcs_stock(_table("S1", 30), _tmcs("S1", 20, 10), jst_threshold=20, tmcs_threshold=50)
    assert len(recs) == 1
    assert recs[0]["actual_stock"] == 30
    assert recs[0]["tmcs_total_sellable_stock"] == 30


def test_low_tmcs_excludes_risk_rows():
    # 聚水潭<20 属风险表，子表排除
    recs = ia.detect_low_tmcs_stock(_table("S1", 5), _tmcs("S1", 10, 10), jst_threshold=20, tmcs_threshold=50)
    assert recs == []


def test_low_tmcs_excludes_when_tmcs_ge_threshold():
    recs = ia.detect_low_tmcs_stock(_table("S1", 30), _tmcs("S1", 30, 20), jst_threshold=20, tmcs_threshold=50)
    assert recs == []  # 猫超可售 50 >= 50
