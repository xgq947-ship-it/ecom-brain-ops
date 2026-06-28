import json
from pathlib import Path
from typing import Any

from ops_cli.platforms.jst import order


class FakeSceneManager:
    root = "/tmp/sessionhub"

    def ensure_scene(self, site: str, scene: str) -> dict[str, Any]:
        return {
            "site": site,
            "scene": scene,
            "url": "https://www.erp321.com/app/order/order/list.aspx",
            "headers": {"Cookie": "sid=test"},
        }


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class FakeClient:
    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, *args: object, **kwargs: object) -> FakeResponse:
        payload = str((kwargs or {}).get("data") or "")
        order_no = "TB10001"
        if "TB40404" in payload:
            order_no = "TB40404"
        rows = [
            {
                "o_id": "10001",
                "so_id": "SO10001",
                "outer_so_id": order_no,
                "logistics_no": "SF123456",
                "logistics_company": "顺丰速运",
                "logistics_status": "已签收",
            }
        ]
        if order_no == "TB40404":
            rows = []
        return FakeResponse({"ReturnValue": json.dumps({"rows": rows}, ensure_ascii=False)})


def test_guess_signed_from_status() -> None:
    assert order._guess_signed("包裹已签收", []) is True
    assert order._guess_signed("", []) is None


def test_normalize_trace_events_from_nested_payload() -> None:
    events = order._normalize_trace_events({"data": [{"time": "10:00", "content": "已揽收"}]})

    assert events == [{"time": "10:00", "content": "已揽收"}]


def test_trace_authorization_challenge_is_not_treated_as_empty_trace() -> None:
    payload = {
        "IsSuccess": False,
        "ReturnValue": {
            "msg": "为了您的数据安全，查询轨迹要求验证身份，已发送验证码到您手机",
            "action": "查询轨迹",
        },
    }
    response = "0|" + json.dumps(payload, ensure_ascii=False)

    try:
        order._parse_acall_response(response)
    except order.LogisticsTraceAuthorizationRequired as exc:
        assert "查询轨迹需要完成短信验证" in str(exc)
    else:
        raise AssertionError("应将查询轨迹短信验证识别为授权错误")


def test_run_order_logistics_from_order_list(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(order, "get_scene_manager", lambda: FakeSceneManager())
    monkeypatch.setattr(order, "build_client", lambda **kwargs: FakeClient())

    response = order.run_order_logistics(outer_order_id="TB10001")

    assert response.success is True
    assert response.command == "order logistics"
    assert response.data["matched_filter"] == "outer_so_id"
    assert response.data["logistics_no"] == "SF123456"
    assert response.data["logistics_company"] == "顺丰速运"
    assert response.data["signed"] is True
    assert Path(response.data["context_path"]).exists()


def test_run_order_logistics_batch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(order, "get_scene_manager", lambda: FakeSceneManager())
    monkeypatch.setattr(order, "build_client", lambda **kwargs: FakeClient())

    response = order.run_order_logistics(outer_order_ids=["TB10001", "TB40404"])

    assert response.success is False
    assert response.command == "order logistics"
    assert response.data["summary"] == {"total": 2, "success": 1, "failed": 1}
    assert response.data["items"][0]["success"] is True
    assert response.data["items"][0]["outer_order_id"] == "TB10001"
    assert response.data["items"][1]["success"] is False
    assert response.data["items"][1]["outer_order_id"] == "TB40404"
    assert "聚水潭未找到指定订单" in response.data["items"][1]["error"]
    assert Path(response.data["context_path"]).exists()


def test_run_order_query_filters_and_normalizes(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(order, "get_scene_manager", lambda: FakeSceneManager())

    rows = [
        {
            "o_id": "OID1",
            "outer_so_id": "TB1",
            "shop_name": "测试店铺",
            "status": "已付款待审核",
            "pay_date": "2026-06-02 10:00:00",
            "remark": "",
            "items": [{"sku_code": "AMY001", "name": "按摩椅 A"}],
        },
        {
            "o_id": "OID2",
            "outer_so_id": "TB2",
            "shop_name": "其他店铺",
            "status": "已付款待审核",
            "pay_date": "2026-06-02 11:00:00",
            "items": [{"sku_code": "AMY002", "name": "按摩椅 B"}],
        },
    ]

    def fake_query_page_rows(*args, **kwargs):
        return rows if kwargs["page"] == 1 else []

    monkeypatch.setattr(order, "_query_page_rows", fake_query_page_rows)
    monkeypatch.setattr(order, "build_client", lambda **kwargs: FakeClient())

    response = order.run_order_query(
        date_value="2026-06-02",
        shop_name="测试店铺",
        status="已付款待审核",
        keyword="按摩椅",
        limit=None,
    )

    assert response.success is True
    assert response.command == "order query"
    assert response.data["summary"]["total"] == 1
    assert response.data["orders"][0]["order_id"] == "OID1"
    assert response.data["orders"][0]["items"][0]["product_code"] == "AMY001"
    assert response.data["orders"][0]["items"][0]["product_name"] == "按摩椅 A"


def test_run_order_query_by_order_id_supports_shipped_alias(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(order, "get_scene_manager", lambda: FakeSceneManager())
    monkeypatch.setattr(order, "build_client", lambda **kwargs: FakeClient())

    row = {
        "o_id": "11875514",
        "so_id": "LP00820708449401",
        "raw_so_id": "LP00820708449401",
        "pre_so_id": "LP00820708449401",
        "outer_so_id": "5118946273259005840",
        "shop_name": "（猫超）福安市启明工贸有限公司（肖国清）",
        "status": "已发货",
        "pay_date": "2026-06-02 12:37:43",
        "remark": "",
        "items": [{"sku_id": "SUAMKBKBA4701", "name": "苏泊尔颈椎按摩器"}],
    }

    def fake_query_by_identifier(*args, **kwargs):
        return [row], "so_id"

    monkeypatch.setattr(order, "_query_order_rows_by_identifier", fake_query_by_identifier)

    response = order.run_order_query(
        date_value="2026-06-02",
        order_ids=["LP00820708449401"],
        shop_name="（猫超）福安市启明工贸有限公司（肖国清）",
        status="线上已发货",
        keyword="按摩",
        limit=None,
    )

    assert response.success is True
    assert response.data["summary"]["total"] == 1
    assert response.data["filters"]["order_ids"] == ["LP00820708449401"]
    assert response.data["orders"][0]["order_id"] == "11875514"
    assert response.data["orders"][0]["outer_order_id"] == "5118946273259005840"
    assert response.data["orders"][0]["status"] == "已发货"
    assert response.data["orders"][0]["items"][0]["product_code"] == "SUAMKBKBA4701"


def test_run_order_remark_matches_lp_order_id(monkeypatch) -> None:
    class FakeManager:
        def ensure_scene(self, site, scene):
            return {"headers": {"cookie": "a=b"}, "url": "https://www.erp321.com/app/order/order/list.aspx"}

    monkeypatch.setattr(order, "get_scene_manager", lambda: FakeManager())
    monkeypatch.setattr(order, "_normalize_orders", lambda **kwargs: (["LP00820708449401"], None))
    monkeypatch.setattr(order, "_write_failed_orders", lambda results, prefix="jst_remark_failed_orders": None)

    def fake_query_by_identifier(*args, **kwargs):
        return [{"o_id": "11875514", "so_id": "LP00820708449401"}], "so_id"

    monkeypatch.setattr(order, "_query_order_rows_by_identifier", fake_query_by_identifier)
    monkeypatch.setattr(order, "_append_remark", lambda *args, **kwargs: None)

    response = order.run_order_remark(
        order_ids=["LP00820708449401"],
        input_path=None,
        limit=None,
        execute=False,
        remark_text="测试备注",
    )

    assert response.success is True
    assert response.data["summary"]["success"] == 1
    assert response.data["results"][0]["o_id"] == "11875514"


def test_append_remark_uses_plain_remark_type_by_default(monkeypatch) -> None:
    captured: dict = {}

    def fake_request_jst(client, url, cookie, method, callback_param, *, form_template=None):
        captured["method"] = method
        captured["callback_param"] = callback_param
        return {}

    monkeypatch.setattr(order, "_request_jst", fake_request_jst)

    order._append_remark(object(), "https://www.erp321.com/app/order/order/list.aspx", "sid=test", "11875514", "测试备注")

    assert captured["method"] == "SaveAppendRemarks"
    assert captured["callback_param"]["Args"][0] == order.REMARK_TYPE
    assert order.REMARK_TYPE == "1"


def test_order_label_keeps_label_remark_type(monkeypatch) -> None:
    class FakeManager:
        def ensure_scene(self, site, scene):
            return {"headers": {"cookie": "a=b"}, "url": "https://www.erp321.com/app/order/order/list.aspx"}

    monkeypatch.setattr(order, "get_scene_manager", lambda: FakeManager())
    monkeypatch.setattr(order, "_normalize_orders", lambda **kwargs: (["TB1"], None))
    monkeypatch.setattr(order, "_query_order_o_ids", lambda *args, **kwargs: ["OID1"])
    monkeypatch.setattr(order, "_set_labels", lambda *args, **kwargs: None)
    monkeypatch.setattr(order, "_write_failed_orders", lambda results, prefix="jst_tag_failed_orders": None)

    remark_types: list[str] = []

    def fake_append_remark(*args, **kwargs):
        remark_types.append(kwargs["remark_type"])

    monkeypatch.setattr(order, "_append_remark", fake_append_remark)

    response = order.run_order_label(
        order_ids=["TB1"],
        input_path=None,
        limit=None,
        execute=True,
        labels=order.DEFAULT_LABELS,
        remark_text=order.DEFAULT_REMARK_TEXT,
    )

    assert response.success is True
    assert remark_types == [order.LABEL_REMARK_TYPE]
    assert order.LABEL_REMARK_TYPE == "2"


def test_normalize_orders_supports_text_input(tmp_path: Path) -> None:
    input_path = tmp_path / "orders.txt"
    input_path.write_text("TB10001\nTB10002\n\nTB10001\n", encoding="utf-8")

    orders, resolved_input = order._normalize_orders(order_ids=[], input_path=str(input_path), limit=2)

    assert orders == ["TB10001", "TB10002"]
    assert resolved_input == str(input_path.resolve())
