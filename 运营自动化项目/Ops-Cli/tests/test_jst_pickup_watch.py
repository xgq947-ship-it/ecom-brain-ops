from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from ops_cli.platforms.jst import pickup_watch


def test_has_pickup_record_matches_configured_trace_keyword() -> None:
    matched, keyword = pickup_watch.detect_pickup_record(
        [{"time": "2026-05-27 09:48:00", "content": "快件已由顺丰收取"}],
        ["已揽收", "快件已由顺丰收取"],
    )

    assert matched is True
    assert keyword == "快件已由顺丰收取"


def test_actual_trace_status_yi_lan_jian_is_recognized_from_config() -> None:
    matched, keyword = pickup_watch.detect_pickup_record([{"StatusSrc": "已揽件"}])

    assert matched is True
    assert keyword == "已揽件"


def test_dry_run_produces_orders_for_risk_evaluation() -> None:
    response = pickup_watch.run_pickup_watch(hours=48, dry_run=True)

    assert response.success is True
    assert response.command == "order pickup-watch"
    assert response.data["dry_run"] is True
    assert len(response.data["orders"]) >= 9
    assert any(item["has_pickup_record"] for item in response.data["orders"])
    assert any("猫超" in item["platform"] for item in response.data["orders"])
    assert any(item["logistics_no"] and not item["logistics_traces"] for item in response.data["orders"])


class FakeSceneManager:
    def ensure_scene(self, site: str, scene: str) -> dict[str, object]:
        return {"url": "https://www.erp321.com/app/order/order/list.aspx", "headers": {"Cookie": "sid=test"}}


class FakeClient:
    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_fetch_paid_orders_reuses_order_logistics_resolver(monkeypatch) -> None:
    now = datetime.fromisoformat("2026-05-27T19:00:00+08:00")
    rows = [
        {
            "shop_name": "（猫超）店铺",
            "outer_so_id": "TM001",
            "o_id": "J001",
            "order_from": "TMMARKET",
            "pay_date": "2026-05-27 08:00:00",
            "l_id": "SF001",
            "logistics_company": "顺丰速运",
            "is_paid": True,
            "status": "已发货",
        },
        {"o_id": "UNPAID", "pay_date": "2026-05-27 09:00:00", "is_paid": False},
        {"o_id": "OLD", "pay_date": "2026-05-25 08:00:00", "is_paid": True},
    ]

    monkeypatch.setattr(pickup_watch, "get_scene_manager", lambda: FakeSceneManager())
    monkeypatch.setattr(pickup_watch, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(pickup_watch, "_query_page_rows", lambda *args, **kwargs: rows if kwargs["page"] == 1 else [])
    monkeypatch.setattr(
        pickup_watch.jst_order,
        "resolve_logistics_from_row",
        lambda client, session, row: {
            "logistics_no": "SF001",
            "logistics_company": "顺丰速运",
            "logistics_status": "已发货",
            "trace_events": [{"detail": "已揽收且含额外明细", "StatusSrc": "已揽件", "created": "2026-05-27 10:00:00"}],
        },
    )

    orders = pickup_watch._fetch_paid_orders(hours=48, shop_name=None, debug=False, now=now)

    assert len(orders) == 1
    assert orders[0]["platform_order_no"] == "TM001"
    assert orders[0]["jst_pay_time"] == "2026-05-27T08:00:00+08:00"
    assert orders[0]["has_pickup_record"] is True
    assert orders[0]["pickup_matched_keyword"] == "已揽收"
    assert orders[0]["logistics_traces"] == [{"status": "已揽件", "time": "2026-05-27 10:00:00"}]
    assert "receiver_address" not in orders[0]["raw"]


def test_real_run_defaults_to_configured_monitor_store(monkeypatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(pickup_watch, "get_config", lambda: SimpleNamespace(jst_order_stats_store="配置猫超店铺"))
    monkeypatch.setattr(
        pickup_watch,
        "_fetch_paid_orders",
        lambda **kwargs: observed.update(kwargs) or [],
    )

    response = pickup_watch.run_pickup_watch(hours=48, dry_run=False)

    assert response.data["shop_name"] == "配置猫超店铺"
    assert observed["shop_name"] == "配置猫超店铺"


def test_order_without_logistics_number_is_unpicked_without_trace_query(monkeypatch) -> None:
    now = datetime.fromisoformat("2026-05-27T19:00:00+08:00")
    rows = [
        {
            "shop_name": "（猫超）店铺",
            "outer_so_id": "TM002",
            "o_id": "J002",
            "order_from": "TMMARKET",
            "pay_date": "2026-05-27 08:00:00",
            "is_paid": True,
            "status": "待发货",
        }
    ]

    monkeypatch.setattr(pickup_watch, "get_scene_manager", lambda: FakeSceneManager())
    monkeypatch.setattr(pickup_watch, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(pickup_watch, "_query_page_rows", lambda *args, **kwargs: rows if kwargs["page"] == 1 else [])

    def fail_if_queried(*args, **kwargs):
        raise AssertionError("没有快递单号时不应查询物流轨迹")

    monkeypatch.setattr(pickup_watch.jst_order, "resolve_logistics_from_row", fail_if_queried)

    orders = pickup_watch._fetch_paid_orders(hours=48, shop_name=None, debug=False, now=now)

    assert orders[0]["logistics_no"] == ""
    assert orders[0]["has_pickup_record"] is False
    assert orders[0]["logistics_traces"] == []
