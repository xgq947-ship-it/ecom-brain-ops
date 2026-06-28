from __future__ import annotations

from urllib.parse import urlparse, parse_qs

import pytest

from ops_cli.capabilities import capability_ids, get_capability
from ops_cli.cli import app  # noqa: F401
from ops_cli.platforms.tmcs import price_competitiveness as pc


# 贴近真实「价格竞争力」结果表格的样本片段（商品编码 = ItemID）。
PAGE_SAMPLE = (
    "商品信息 规格属性 市场同款商品价格信息 ... "
    "奥克斯筋膜枪 ItemID:1042043620771 SkuID:6061124933141 "
    "苏泊尔足疗机 ItemID:1040897246648 SkuID:6061431084236 "
    "共 25 条数据 每页显示：20"
)


def test_capability_registered() -> None:
    registered = capability_ids()
    assert "tmcs.price-competitiveness.lookup" in registered
    assert "tmcs.price-competitiveness.list" in registered
    assert "tmcs.price-competitiveness.learn" in registered

    spec = get_capability("tmcs.price-competitiveness.lookup")
    assert spec.platform == "tmcs"
    assert spec.command == "price-competitiveness lookup"
    assert "price_competitiveness_lookup" in spec.scenes

    list_spec = get_capability("tmcs.price-competitiveness.list")
    assert list_spec.command == "price-competitiveness list"


def test_nav_url_routes_inner_frame() -> None:
    # 整页导航地址用 ?frameUrl= 路由内层 ProductPriceForce 页。
    parsed = urlparse(pc.TMCS_PRICE_COMPETITIVENESS_NAV_URL)
    assert parsed.hostname == "web.txcs.tmall.com"
    inner = parse_qs(parsed.query)["frameUrl"][0]
    inner_parsed = urlparse(inner)
    assert inner_parsed.hostname == "tbmc.portal.tmall.com"
    assert "ProductPriceForce" in inner_parsed.path


def test_extract_item_ids() -> None:
    assert pc.extract_item_ids(PAGE_SAMPLE) == ["1042043620771", "1040897246648"]
    assert pc.extract_item_ids("没有任何编码") == []


def test_parse_total_rows() -> None:
    assert pc.parse_total_rows(PAGE_SAMPLE) == 25
    assert pc.parse_total_rows("共 0 条数据") == 0
    assert pc.parse_total_rows("无总数") is None


def test_is_empty_result() -> None:
    assert pc.is_empty_result("查询结果：暂无数据") is True
    assert pc.is_empty_result(PAGE_SAMPLE) is False


def test_evaluate_lookup_exists_exact_match() -> None:
    rows = [
        {"item_id": "1042043620771", "sku_id": "6061124933141", "title": "按摩器"},
    ]
    verdict = pc.evaluate_lookup("1042043620771", rows)
    assert verdict["exists"] is True
    assert verdict["matched_items"][0]["item_id"] == "1042043620771"
    assert verdict["matched_items"][0]["sku_id"] == "6061124933141"
    assert verdict["total_rows"] == 1


def test_evaluate_lookup_empty_is_false() -> None:
    verdict = pc.evaluate_lookup("999999999999", [])
    assert verdict["exists"] is False
    assert verdict["matched_items"] == []
    assert verdict["total_rows"] == 0


def test_evaluate_lookup_other_codes_only_is_false() -> None:
    # 表格里只有其它商品编码 → 不算存在（逐行精确匹配，不是「有没有数据」）。
    rows = [
        {"item_id": "1040897246648", "sku_id": "a", "title": "甲"},
        {"item_id": "1046002963130", "sku_id": "b", "title": "乙"},
    ]
    verdict = pc.evaluate_lookup("1042043620771", rows)
    assert verdict["exists"] is False
    assert verdict["matched_items"] == []
    assert verdict["total_rows"] == 2


def test_evaluate_lookup_dedupes_same_item_sku() -> None:
    rows = [
        {"item_id": "100", "sku_id": "s1", "title": "x"},
        {"item_id": "100", "sku_id": "s1", "title": "x"},
        {"item_id": "100", "sku_id": "s2", "title": "x"},
    ]
    verdict = pc.evaluate_lookup("100", rows)
    assert verdict["exists"] is True
    assert len(verdict["matched_items"]) == 2  # (100,s1) 与 (100,s2)


def test_dry_run_returns_simulated(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = pc.run_price_competitiveness_lookup(product_code="TEST123", dry_run=True)

    assert response.success is True
    assert response.platform == "tmcs"
    assert response.command == "price-competitiveness lookup"
    data = response.data
    assert data["product_code"] == "TEST123"
    assert data["exists"] is False
    assert data["matched_items"] == []
    assert data["total_rows"] == 0
    assert data["simulated"] is True
    assert data["source"] == "simulated"
    assert data["dry_run"] is True
    assert data["screenshot_path"] is None
    assert data["scene"].endswith("/price_competitiveness_lookup")
    assert data["context_path"].endswith(".json")


def test_list_dry_run_returns_simulated(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = pc.run_price_competitiveness_list(dry_run=True)

    assert response.success is True
    assert response.command == "price-competitiveness list"
    data = response.data
    assert data["rows"] == []
    assert data["total_rows"] == 0
    assert data["simulated"] is True
    assert data["source"] == "simulated"
    assert data["dry_run"] is True
    assert data["captured_at"]
    assert data["scene"].endswith("/price_competitiveness_lookup")


def test_missing_product_code_raises() -> None:
    with pytest.raises(RuntimeError, match="PRODUCT_CODE_REQUIRED"):
        pc.run_price_competitiveness_lookup(product_code="  ", dry_run=True)


def test_learn_returns_page_dom_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = pc.learn_price_competitiveness_lookup(force=False)
    assert response.success is True
    assert response.data["mode"] == "page_dom"
    assert response.data["scene"] == "price_competitiveness_lookup"
