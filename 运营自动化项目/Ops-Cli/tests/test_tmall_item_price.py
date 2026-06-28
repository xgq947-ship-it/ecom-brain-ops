from __future__ import annotations

from ops_cli.capabilities import capability_ids, get_capability
from ops_cli.cli import app  # noqa: F401 - triggers platform discovery
from ops_cli.platforms.tmall import item_price


def test_capability_registered() -> None:
    assert "tmall.price.get" in capability_ids()
    spec = get_capability("tmall.price.get")
    assert spec.platform == "tmall"
    assert spec.command == "price get"
    assert spec.recovery_policy == "never"


def test_parse_money() -> None:
    assert item_price.parse_money("¥1,299.00 起") == 1299.0
    assert item_price.parse_money("899") == 899.0
    assert item_price.parse_money("无价") is None
    assert item_price.parse_money(None) is None


def test_parse_price_from_text() -> None:
    price, raw = item_price.parse_price_from_text("券后 ¥899.5 到手")
    assert price == 899.5 and raw == "¥899.5"
    assert item_price.parse_price_from_text("没有价格")[0] is None


def test_parse_labeled_deal_price_prefers_coupon_price() -> None:
    text = """
    平台加补后
    ￥
    360.47
    活动价￥699
    官方立减248.48元
    可再享：领消费券后的￥337.22
    """

    price, raw = item_price.parse_labeled_deal_price_from_text(text)

    assert price == 337.22
    assert "337.22" in raw


def test_parse_labeled_deal_price_ignores_non_price_numbers() -> None:
    # 标签后紧跟的「返5元/立减20元」不带 ¥，不能被误抓；只认带 ¥ 的真实到手价。
    text = "券后返5元 立减20元 到手价 ¥380.00"
    price, raw = item_price.parse_labeled_deal_price_from_text(text)
    assert price == 380.0
    assert "380" in raw

    # 标签后没有任何带 ¥ 的金额时，返回空（不再凑一个邻近数字）。
    assert item_price.parse_labeled_deal_price_from_text("券后再返5元，详情见活动页") == (None, "")


def test_parse_deal_price_from_block() -> None:
    # 无直降：活动价即到手价。
    assert item_price.parse_deal_price_from_block("活动价￥588.81")[0] == 588.81
    # 超市推荐价 − 直降 = 到手价（624 - 126 = 498）。
    price, raw = item_price.parse_deal_price_from_block("超市推荐￥624起直降126元")
    assert price == 498.0 and "624" in raw
    # 立减也参与减法。
    assert item_price.parse_deal_price_from_block("￥1000立减200元")[0] == 800.0
    # 直降金额异常（减成 ≤0）时退回参考价，不算负数。
    assert item_price.parse_deal_price_from_block("￥100直降500元")[0] == 100.0
    # 无 ¥金额返回空。
    assert item_price.parse_deal_price_from_block("超市推荐 直降126元") == (None, "")
    assert item_price.parse_deal_price_from_block("") == (None, "")


def test_needs_full_url_context_rejects_plain_price_with_promo_hints() -> None:
    text = """
    618狂欢节
    领取政府补贴7%
    立即领取
    官方立减248.48元
    ￥999
    """

    assert item_price.needs_full_url_context(text, "￥999", "") is True


def test_needs_full_url_context_allows_labeled_price_or_source_url() -> None:
    text = "平台加补后 ￥ 313.87\n领取政府补贴7%\n立即领取"

    assert item_price.needs_full_url_context(text, "平台加补后 ￥ 313.87", "") is False
    assert (
        item_price.needs_full_url_context(
            text,
            "￥999",
            "https://detail.tmall.com/item.htm?id=1052534376394&mi_id=abc",
        )
        is False
    )


def test_needs_full_url_context_rejects_chaoshi_bare_price_without_url() -> None:
    assert (
        item_price.needs_full_url_context(
            "商品标题\n￥999",
            "￥999",
            "",
            page_url="https://chaoshi.detail.tmall.com/item.htm?id=1053519004987",
        )
        is True
    )


def test_parse_item_refs_preserves_source_url() -> None:
    refs = item_price.parse_item_refs(
        "https://detail.tmall.com/item.htm?id=1052534376394&mi_id=abc,762065566026"
    )

    assert refs == [
        {
            "item_id": "1052534376394",
            "source_url": "https://detail.tmall.com/item.htm?id=1052534376394&mi_id=abc",
        },
        {"item_id": "762065566026", "source_url": ""},
    ]


def test_complete_item_refs_with_tmcs_activity_urls(monkeypatch) -> None:
    monkeypatch.setattr(
        item_price,
        "_query_tmall_activity_urls",
        lambda ids: {"101": "https://detail.tmall.com/item.htm?id=101&mi_id=mid-101"},
    )
    refs = [
        {"item_id": "101", "source_url": ""},
        {"item_id": "102", "source_url": "https://detail.tmall.com/item.htm?id=102&mi_id=given"},
    ]

    completed = item_price.complete_item_refs_with_activity_urls(refs)

    assert completed == [
        {"item_id": "101", "source_url": "https://detail.tmall.com/item.htm?id=101&mi_id=mid-101"},
        {"item_id": "102", "source_url": "https://detail.tmall.com/item.htm?id=102&mi_id=given"},
    ]


def test_extract_mtop_price_from_api_stack_price_module() -> None:
    payload = {
        "ret": ["SUCCESS::调用成功"],
        "data": {
            "apiStack": [
                {
                    "name": "esi",
                    "value": json_dump(
                        {
                            "price": {
                                "price": {
                                    "priceText": "¥588.81",
                                },
                            },
                            "skuCore": {
                                "sku2info": {
                                    "0": {
                                        "price": {
                                            "priceText": "¥599.00",
                                        }
                                    }
                                }
                            },
                        }
                    ),
                }
            ]
        },
    }

    price, raw = item_price.extract_price_from_mtop_payload(payload)

    assert price == 588.81
    assert raw == "¥588.81"


def test_extract_mtop_price_falls_back_to_sku_core() -> None:
    payload = {
        "ret": ["SUCCESS::调用成功"],
        "data": {
            "apiStack": [
                {
                    "value": json_dump(
                        {
                            "skuCore": {
                                "sku2info": {
                                    "6167397490222": {
                                        "price": {
                                            "priceText": "券后 ¥624",
                                        }
                                    }
                                }
                            }
                        }
                    ),
                }
            ]
        },
    }

    price, raw = item_price.extract_price_from_mtop_payload(payload)

    assert price == 624.0
    assert raw == "券后 ¥624"


def test_extract_mtop_price_ignores_punish_response() -> None:
    payload = {
        "ret": ["RGV587_ERROR::SM::哎哟喂,被挤爆啦,请稍后重试!"],
        "data": {"url": "https://bixi.alicdn.com/punish/punish.html"},
    }

    assert item_price.extract_price_from_mtop_payload(payload) == (None, "")


def test_classify_page() -> None:
    assert item_price.classify_page("https://login.taobao.com/x", "") == "login_required"
    assert item_price.classify_page("https://x/?captcha=1", "") == "captcha"
    assert item_price.classify_page("https://x", "请向右滑动完成安全验证") == "captcha"
    assert item_price.classify_page("https://x", "很抱歉，您查看的商品找不到了") == "item_not_found"
    assert item_price.classify_page("https://detail.tmall.com/item.htm?id=1", "正常商品页") is None


def test_dry_run_returns_simulated_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    resp = item_price.run_item_price(
        item_ids="741234567890,752345678901",
        screenshot_dir=str(tmp_path / "shots"),
        dry_run=True,
    )
    assert resp.success is True
    assert resp.platform == "tmall"
    assert resp.command == "price get"
    data = resp.data
    assert data["dry_run"] is True and data["simulated"] is True
    assert data["count"] == 2
    row = data["rows"][0]
    assert row["item_id"] == "741234567890"
    assert row["capture_status"] == "ok"
    assert row["realtime_price"] == 1299.0
    assert row["screenshot_path"].endswith(".png")


def json_dump(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
