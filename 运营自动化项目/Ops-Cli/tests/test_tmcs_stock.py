from __future__ import annotations

import pytest

from ops_cli.capabilities import bind_capability_execution, get_capability
from ops_cli.platforms.tmcs import shared
from ops_cli.platforms.tmcs import stock


def test_load_stock_template_uses_current_inventory_scene(monkeypatch) -> None:
    monkeypatch.setattr(
        stock,
        "load_scene_or_fail",
        lambda *args, **kwargs: {"token": "fresh"},
        raising=False,
    )

    assert stock._load_stock_template() == {"inventory_search": {"token": "fresh"}}


def test_query_stock_maps_inventory_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        stock,
        "_load_stock_template",
        lambda: {
            "defaults": {"warehouse_code": "mc_aokesi_suolong"},
            "inventory_search": {"url": "https://example.test/search", "headers": {}},
        },
    )
    monkeypatch.setattr(stock, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})

    def fake_search_inventory_rows(*, search_scene, warehouse_code, item_id=None, sku_id=None):
        assert warehouse_code == "mc_aokesi_suolong"
        assert sku_id is None
        if item_id == "1052534376394":
            return [
                {
                    "itemId": 1052534376394,
                    "skuId": 6247519890565,
                    "supplierScItemId": "SG-001",
                    "scItemId": "PLATFORM-001",
                    "barCode": "BAR-001",
                    "merchantGoodsCode": "AOK-SKU-001",
                    "storeCode": "mc_aokesi_suolong",
                }
            ]
        return []

    monkeypatch.setattr(stock, "_search_inventory_rows", fake_search_inventory_rows)

    rows = stock.query_stock(item_ids=["1052534376394", "missing"], warehouse_code="mc_aokesi_suolong")

    assert rows == [
        {
            "platform_item_id": "1052534376394",
            "platform_sku_id": "6247519890565",
            "supplier_goods_id": "SG-001",
            "merchant_goods_code": "BAR-001",
        }
    ]


def test_normalize_item_ids_dedupes_and_preserves_order() -> None:
    assert stock.normalize_item_ids(" 123,234,123,,345 ") == ["123", "234", "345"]


def test_standardize_stock_row_prefers_supplier_side_goods_id() -> None:
    row = {
        "itemId": 1053519004987,
        "skuId": 6087220948149,
        "supplierScItemId": 1040893114685,
        "scItemId": 1052514261839,
        "barCode": "SUZBHLYZHH1001A",
        "merchantGoodsCode": "SUZBHLYZHH1001",
    }

    assert stock.standardize_stock_row(row) == {
        "platform_item_id": "1053519004987",
        "platform_sku_id": "6087220948149",
        "supplier_goods_id": "1040893114685",
        "merchant_goods_code": "SUZBHLYZHH1001A",
    }


def test_tmcs_request_raises_auth_failure_for_login_html(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        content = "<html><head><title>登录</title></head></html>".encode()

        def raise_for_status(self) -> None:
            return None

        def json(self):
            raise ValueError("not json")

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def request(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(shared, "build_client", lambda **kwargs: FakeClient())

    with pytest.raises(RuntimeError, match="登录"):
        shared.tmcs_request("POST", "https://example.test/search", headers={}, json_body={})


def test_query_stock_recovers_when_scene_check_fails(monkeypatch) -> None:
    checks = 0
    learned: list[bool] = []
    refreshed: list[str] = []
    monkeypatch.setattr(stock, "_load_stock_template", lambda: {"inventory_search": {}})

    def fake_check_scene_or_fail(*args, **kwargs):
        nonlocal checks
        checks += 1
        if checks == 1:
            # 模拟 scene 失效：错误命中 auth marker（“登录”），应触发 9222 重沉淀
            raise RuntimeError("Scene 校验失败：疑似返回登录页或权限页。请先运行 `ops tmcs inventory adjust-learn`。")
        return {"status": "valid"}

    monkeypatch.setattr(stock, "check_scene_or_fail", fake_check_scene_or_fail)
    monkeypatch.setattr(stock, "learn_inventory_adjust", lambda *, force: learned.append(force))
    monkeypatch.setattr(stock, "mark_scene_refreshed", lambda scene: refreshed.append(scene))
    monkeypatch.setattr(
        stock,
        "_search_inventory_rows",
        lambda **kwargs: [{"itemId": "1055840134894", "supplierScItemId": "SG-9", "barCode": "BAR-9"}],
    )

    with bind_capability_execution(get_capability("tmcs.stock.query"), interactive_login=True):
        rows = stock.query_stock(item_ids=["1055840134894"], warehouse_code="mc_aokesi_suolong")

    assert rows[0]["platform_item_id"] == "1055840134894"
    assert checks == 2
    assert learned == [True]
    assert refreshed == [stock.TMCS_INVENTORY_SEARCH_SCENE]


def test_query_stock_refreshes_inventory_scene_after_auth_failure(monkeypatch) -> None:
    attempts = 0
    refreshed: list[str] = []
    learned: list[bool] = []
    monkeypatch.setattr(stock, "_load_stock_template", lambda: {"inventory_search": {}})
    monkeypatch.setattr(stock, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(stock, "learn_inventory_adjust", lambda *, force: learned.append(force))
    monkeypatch.setattr(stock, "mark_scene_refreshed", lambda scene: refreshed.append(scene))

    def fake_search_inventory_rows(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("猫超登录态失效：接口返回登录页面")
        return [{"itemId": "1052305450766", "supplierScItemId": "SG-001", "barCode": "BAR-001"}]

    monkeypatch.setattr(stock, "_search_inventory_rows", fake_search_inventory_rows)

    with bind_capability_execution(get_capability("tmcs.stock.query"), interactive_login=True):
        rows = stock.query_stock(item_ids=["1052305450766"], warehouse_code="mc_aokesi_suolong")

    assert rows[0]["platform_item_id"] == "1052305450766"
    assert attempts == 2
    assert learned == [True]
    assert refreshed == [stock.TMCS_INVENTORY_SEARCH_SCENE]
