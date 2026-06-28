import json

import pytest
from openpyxl import Workbook

from ops_cli.platforms.tmcs import product


def _build_goods_workbook(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "商品列表"
    ws.append(["货品编码", "条码", "名称"])
    for row in rows:
        ws.append(row)
    wb.save(path)


def _build_jst_workbook(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "商品资料"
    ws.append(["商品编码", "品牌", "名称"])
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_tmcs_product_sync_requires_template(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="未找到猫超商品同步模板"):
        product.run_product_sync()


def test_tmcs_product_template_filters_cookies_by_target_url(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)

    product._write_template(
        search_scene={
            "url": "https://merchandise-mc.cbbs.tmall.com/webapi/merchandise/item/searchItem",
            "method": "POST",
            "headers": {"cookie": "too=many", "content-length": "10", "accept": "application/json"},
            "cookies": [
                {"name": "tmall", "value": "1", "domain": ".tmall.com"},
                {"name": "cbbs", "value": "2", "domain": ".cbbs.tmall.com"},
                {"name": "google", "value": "3", "domain": ".google.com"},
            ],
            "post_data_form": {},
        },
        export_scene={
            "url": "https://tools.cbbs.tmall.com/gei/export/task/demo",
            "method": "POST",
            "headers": {"cookie": "too=many"},
            "cookies": [
                {"name": "tools", "value": "1", "domain": ".cbbs.tmall.com"},
                {"name": "openai", "value": "2", "domain": ".openai.com"},
            ],
            "post_data_form": {},
        },
    )

    template = json.loads((tmp_path / "data" / "tmcs" / "product_sync_template.json").read_text(encoding="utf-8"))

    assert template["search"]["headers"]["cookie"] == "tmall=1; cbbs=2"
    assert template["export"]["headers"]["cookie"] == "tools=1"
    assert "content-length" not in template["search"]["headers"]


def test_tmcs_product_sync_use_local_only(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    import_path = tmp_path / "猫超商品列表导出.xlsx"
    latest_path = tmp_path / "猫超商品列表导出 (最新）.xlsx"
    jst_path = tmp_path / "聚水潭商品资料（最新）.xlsx"

    _build_goods_workbook(latest_path, [["A1", "OLD-1", "老商品"]])
    _build_goods_workbook(import_path, [["A1", "OLD-1", "老商品"], ["B1", "SKU-B1", "新商品"]])
    _build_jst_workbook(jst_path, [["NEW-SKU-B1", "奥克斯", "匹配商品"]])

    template_path = tmp_path / "data" / "tmcs" / "product_sync_template.json"
    template_path.write_text(
        json.dumps(
            {
                "defaults": {
                    "import_path": str(import_path),
                    "latest_path": str(latest_path),
                    "jst_path": str(jst_path),
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(product, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(
        product,
        "load_scene_or_fail",
        lambda *args, **kwargs: {
            "headers": {"cookie": "a=b"},
            "method": "POST",
            "url": "https://example.com",
            "post_data_form": {"_scm_token_": "x", "query": "{}"},
        },
    )

    result = product.run_product_sync(use_local_only=True)

    assert result.data["used_backend_export"] is False
    assert result.data["new_rows"] == 1
    assert result.data["output_path"] == str(latest_path)


def test_tmcs_product_sync_force_refresh_downloads(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    import_path = tmp_path / "猫超商品列表导出.xlsx"
    latest_path = tmp_path / "猫超商品列表导出 (最新）.xlsx"
    jst_path = tmp_path / "聚水潭商品资料（最新）.xlsx"

    _build_goods_workbook(latest_path, [["A1", "OLD-1", "老商品"]])
    _build_goods_workbook(import_path, [["A1", "OLD-1", "老商品"]])
    _build_jst_workbook(jst_path, [["SKU-B1", "奥克斯", "匹配商品"]])

    downloaded = tmp_path / "downloaded.xlsx"
    _build_goods_workbook(downloaded, [["A1", "OLD-1", "老商品"], ["B1", "BAR-B1", "新商品"]])

    template_path = tmp_path / "data" / "tmcs" / "product_sync_template.json"
    template_path.write_text(
        json.dumps(
            {
                "defaults": {
                    "import_path": str(import_path),
                    "latest_path": str(latest_path),
                    "jst_path": str(jst_path),
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(product, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(
        product,
        "load_scene_or_fail",
        lambda *args, **kwargs: {
            "headers": {"cookie": "a=b"},
            "method": "POST",
            "url": "https://example.com",
            "post_data_form": {"_scm_token_": "x", "query": "{}"},
        },
    )

    def fake_download_goods_export(**kwargs):
        destination = kwargs["destination"]
        destination.write_bytes(downloaded.read_bytes())
        return {"export_task_id": "task-1", "download_size": len(downloaded.read_bytes())}

    monkeypatch.setattr(product, "_download_goods_export", fake_download_goods_export)

    result = product.run_product_sync(force_refresh=True)

    assert result.data["used_backend_export"] is True
    assert result.data["downloaded"] is True
    assert result.data["new_rows"] == 1


def test_tmcs_product_sync_retries_after_auth_refresh(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    import_path = tmp_path / "猫超商品列表导出.xlsx"
    latest_path = tmp_path / "猫超商品列表导出 (最新）.xlsx"
    jst_path = tmp_path / "聚水潭商品资料（最新）.xlsx"
    _build_goods_workbook(latest_path, [["A1", "OLD-1", "老商品"]])
    _build_goods_workbook(import_path, [["A1", "OLD-1", "老商品"]])
    _build_jst_workbook(jst_path, [["SKU-B1", "奥克斯", "匹配商品"]])

    (tmp_path / "data" / "tmcs" / "product_sync_template.json").write_text(
        json.dumps({"defaults": {"import_path": str(import_path), "latest_path": str(latest_path), "jst_path": str(jst_path)}}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(product, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(
        product,
        "load_scene_or_fail",
        lambda *args, **kwargs: {
            "headers": {"cookie": "a=b"},
            "method": "POST",
            "url": "https://example.com",
            "post_data_form": {"_scm_token_": "x", "query": "{}"},
        },
    )
    refresh_calls = {"count": 0}
    monkeypatch.setattr(product, "learn_product_sync", lambda force=False: refresh_calls.__setitem__("count", refresh_calls["count"] + 1))

    attempts = {"count": 0}

    def fake_download_goods_export(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("401 Unauthorized")
        destination = kwargs["destination"]
        _build_goods_workbook(destination, [["A1", "OLD-1", "老商品"], ["B1", "BAR-B1", "新商品"]])
        return {"export_task_id": "task-1", "download_size": len(destination.read_bytes())}

    monkeypatch.setattr(product, "_download_goods_export", fake_download_goods_export)

    result = product.run_product_sync(force_refresh=True)

    assert result.data["downloaded"] is True
    assert result.data["auth_refresh_applied"] is True
    assert refresh_calls["count"] == 1


def test_download_goods_from_search_writes_tmcs_master_shape(tmp_path, monkeypatch) -> None:
    destination = tmp_path / "猫超商品列表导出.xlsx"

    payload = {
        "data": {
            "list": [
                {
                    "itemId": "101",
                    "title": "测试商品",
                    "updownStatus": -1,
                    "barcode": "ITEM-BAR",
                    "supplierName": "供应商A",
                    "shopName": "天猫超市",
                    "brandId": "30844",
                    "brandName": "SUPOR/苏泊尔",
                    "selfBrandId": "400",
                    "selfBrandName": "自营品牌",
                    "categoryName": "按摩器",
                    "gmtCreate": 1769760269000,
                    "itemChannelType": "直营商品",
                    "stockShareStatus": 1,
                    "auditStatusDesc": "已审核",
                    "categoryManager": "沉汤",
                    "supplierCode": "SUP-1",
                    "selfCategoryId": "21060102",
                    "selfCategoryName": "按摩机/仪",
                    "categoryId": "201157408",
                    "skuVOList": [
                        {"skuId": "SKU-1", "updownStatus": 1, "barcode": "BAR-1", "targetScItemId": "SC-1", "reservePriceCNY": 439.0},
                        {"skuId": "SKU-2", "updownStatus": 0, "barcode": "BAR-2", "targetScItemId": "SC-2", "reservePriceCNY": 429.0},
                    ],
                }
            ]
        }
    }

    monkeypatch.setattr(product, "tmcs_request", lambda *args, **kwargs: (200, payload, b""))

    result = product._download_goods_from_search(
        search_scene={"post_data_form": {}, "headers": {}, "method": "POST", "url": "https://example.com"},
        destination=destination,
    )

    assert result["source"] == "product_search_api_fallback"
    assert result["row_count"] == 2

    header, rows = product._load_sheet_data(destination)
    assert header == product.TMCS_MASTER_HEADERS
    assert rows[0][:11] == ["101", "测试商品", "下架", "SKU-1", "上架", None, "BAR-1", 439.0, "供应商A", "天猫超市", "SC-1"]
    assert rows[1][:11] == ["101", "测试商品", "下架", "SKU-2", "下架", None, "BAR-2", 429.0, "供应商A", "天猫超市", "SC-2"]


def test_build_tmall_activity_url_from_tmcs_row_mid() -> None:
    row = {"itemId": "1053519004987", "mid": "00001Z1Whxlyk92m6_mpuGTQQXHshPx"}

    assert product.build_tmall_activity_url_from_tmcs_row(row) == (
        "https://detail.tmall.com/item.htm?id=1053519004987&mi_id=00001Z1Whxlyk92m6_mpuGTQQXHshPx"
    )


def test_query_tmall_activity_urls_uses_item_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "tmcs" / "product_sync_template.json").write_text(
        json.dumps(
            {
                "search": {
                    "url": "https://example.com/searchItem",
                    "method": "POST",
                    "headers": {"cookie": "a=b"},
                    "post_data_form": {"sellerId": "725677994", "pageIndex": "9"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen = {}

    def fake_tmcs_request(method, url, *, headers, data_body, timeout=120.0):
        from urllib.parse import parse_qs

        seen["method"] = method
        seen["url"] = url
        body = data_body.decode("utf-8") if isinstance(data_body, bytes) else data_body
        seen["form"] = {k: v[0] for k, v in parse_qs(body).items()}
        return (
            200,
            {
                "data": {
                    "list": [
                        {"itemId": "101", "mid": "mid-101"},
                        {"itemId": "102", "mid": "mid-102"},
                    ]
                }
            },
            b"",
        )

    monkeypatch.setattr(product, "tmcs_request", fake_tmcs_request)

    urls = product.query_tmall_activity_urls(["101", "102"])

    assert seen["method"] == "POST"
    assert seen["url"] == "https://example.com/searchItem"
    assert seen["form"]["itemIds"] == "101,102"
    assert seen["form"]["pageIndex"] == "1"
    assert seen["form"]["pageSize"] == "20"
    assert urls == {
        "101": "https://detail.tmall.com/item.htm?id=101&mi_id=mid-101",
        "102": "https://detail.tmall.com/item.htm?id=102&mi_id=mid-102",
    }


def _row_with_status(header, *, item_code, sku_code, item_status, sku_status, barcode="OLD-BAR"):
    row = [None] * len(header)
    row[header.index("商品编码")] = item_code
    row[header.index("SKU编码")] = sku_code
    row[header.index("商品上下架状态")] = item_status
    row[header.index("SKU上下架状态")] = sku_status
    row[header.index("条码")] = barcode
    return row


def test_sync_shelf_status_updates_existing_rows() -> None:
    header = product.TMCS_MASTER_HEADERS
    # 存量行：商品上架/SKU上架；本次下载：商品下架/SKU下架 → 应被回写
    latest_rows = [
        _row_with_status(header, item_code="101", sku_code="SKU-1", item_status="上架", sku_status="上架"),
        _row_with_status(header, item_code="999", sku_code="SKU-X", item_status="上架", sku_status="上架"),
    ]
    import_rows = [
        _row_with_status(header, item_code="101", sku_code="SKU-1", item_status="下架", sku_status="下架"),
    ]

    stats = product._sync_shelf_status(
        latest_header=header,
        latest_rows=latest_rows,
        import_header=header,
        import_rows=import_rows,
    )

    # 存量行 101/SKU-1 被刷新为下架
    assert latest_rows[0][header.index("商品上下架状态")] == "下架"
    assert latest_rows[0][header.index("SKU上下架状态")] == "下架"
    # 条码不受影响
    assert latest_rows[0][header.index("条码")] == "OLD-BAR"
    # 本次下载里没有的存量行保持不变
    assert latest_rows[1][header.index("商品上下架状态")] == "上架"
    assert stats == {"status_updated": 1, "status_unchanged": 0, "status_not_in_import": 1}


def test_sync_shelf_status_skips_legacy_schema() -> None:
    # 精简表头（无状态列）应安全跳过，不抛异常
    header = ["货品编码", "条码", "名称"]
    rows = [["A1", "BAR-1", "老商品"]]
    stats = product._sync_shelf_status(
        latest_header=header, latest_rows=rows, import_header=header, import_rows=rows
    )
    assert stats == {"status_updated": 0, "status_unchanged": 0, "status_not_in_import": 0}
