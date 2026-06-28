from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ops_cli.platforms.jst import exchange_resend


def _resolved_order(status: str = "线上已发货") -> dict[str, Any]:
    return {
        "found_order": True,
        "matched_filter": "outer_so_id",
        "internal_order_id": "10001",
        "online_order_id": "LP001",
        "order_status": status,
        "shop_name": "测试店铺",
        "items": [{"product_code": "SKU-1", "name": "测试商品"}],
    }


def _patch_successful_order(monkeypatch, tmp_path: Path, *, status: str = "线上已发货") -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(exchange_resend, "_resolve_order", lambda order_no, **kwargs: _resolved_order(status))
    monkeypatch.setattr(exchange_resend, "write_runtime_context", lambda **kwargs: tmp_path / "context.json")


def test_select_order_row_uses_confirmed_eligible_status_when_multiple_rows() -> None:
    rows = [
        {"o_id": "11976048", "status": "异常"},
        {"o_id": "11888452", "status": "已发货"},
    ]

    selected = exchange_resend._select_order_row(rows, eligible_status=("已发货",))

    assert selected == {"o_id": "11888452", "status": "已发货"}


def test_submit_without_confirmed_template_stays_pending(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path)

    response = exchange_resend.submit_order_exchange_resend(
        order_no="LP001",
        mode="resend",
        confirm_order_no="LP001",
        sku_code="SKU-1",
        qty=2,
        execute=True,
    )

    assert response.data["submitted"] is False
    assert response.data["pending_confirmation"]
    assert response.data["final_payload"]["internal_order_id"] == "10001"


def test_submit_with_confirmed_template_posts_rendered_payload(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path)
    template_path = tmp_path / "data" / "jst" / "order_exchange_resend_template.json"
    template_path.parent.mkdir(parents=True)
    template_path.write_text(
        json.dumps(
            {
                "confirmed": True,
                "method": "POST",
                "url": "https://api.example.com/order/exchange-resend",
                "post_data_template": {
                    "orderId": "__O_ID__",
                    "onlineOrderId": "__ONLINE_ORDER_ID__",
                    "mode": "__MODE__",
                    "sku": "__SKU__",
                    "qty": "__QTY__",
                    "reason": "__REASON__",
                    "remark": "__REMARK__",
                },
                "field_map": {},
                "eligible_status": ["线上已发货"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    class FakeResponse:
        text = '{"success": true, "code": 0, "data": {"afterSaleId": "AS001"}}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"success": True, "code": 0, "data": {"afterSaleId": "AS001"}}

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            captured.update({"method": method, "url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(exchange_resend, "_load_order_session", lambda: ({}, "sid=abc", "https://www.erp321.com/app/order/order/list.aspx", {}))
    monkeypatch.setattr(exchange_resend, "build_client", lambda **kwargs: FakeClient())

    response = exchange_resend.submit_order_exchange_resend(
        order_no="LP001",
        mode="resend",
        confirm_order_no="LP001",
        reason="少件",
        remark="补发 2 件",
        sku_code="SKU-1",
        qty=2,
        execute=True,
    )

    assert response.data["submitted"] is True
    assert response.data["result"] == {"success": True, "code": 0, "data": {"afterSaleId": "AS001"}}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.example.com/order/exchange-resend"
    assert captured["json"] == {
        "orderId": "10001",
        "onlineOrderId": "LP001",
        "mode": "resend",
        "sku": "SKU-1",
        "qty": "2",
        "reason": "少件",
        "remark": "补发 2 件",
    }


def test_submit_defaults_missing_sku_to_original_product(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path)
    template_path = tmp_path / "data" / "jst" / "order_exchange_resend_template.json"
    template_path.parent.mkdir(parents=True)
    template_path.write_text(
        json.dumps(
            {
                "confirmed": True,
                "method": "POST",
                "url": "https://api.example.com/order/exchange-resend",
                "post_data_template": {"sku": "__SKU__"},
                "field_map": {},
                "eligible_status": ["线上已发货"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    class FakeResponse:
        text = '{"success": true, "code": 0}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {"success": True, "code": 0}

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(exchange_resend, "_load_order_session", lambda: ({}, "sid=abc", "https://www.erp321.com/app/order/order/list.aspx", {}))
    monkeypatch.setattr(exchange_resend, "build_client", lambda **kwargs: FakeClient())

    response = exchange_resend.submit_order_exchange_resend(
        order_no="LP001",
        mode="resend",
        confirm_order_no="LP001",
        execute=True,
    )

    assert response.data["final_payload"]["sku_code"] == "SKU-1"
    assert captured["json"] == {"sku": "SKU-1"}


def test_exchange_submit_candidate_defaults_to_original_item() -> None:
    candidate = exchange_resend._build_exchange_submit_candidate(
        {
            "action": "exchange",
            "internal_order_id": "10001",
            "online_order_id": "LP001",
            "sku_code": "SKU-1",
            "qty": 1,
            "reason": "质量问题",
            "remark": "",
        }
    )

    assert candidate["confirmed"] is True
    assert candidate["dry_run_only"] is False
    assert candidate["supported_modes"] == ["exchange"]
    assert candidate["submit_enabled_reason"]
    assert candidate["field_map"]["exchange_items_json"] == "__EXCHANGE_ITEMS_JSON__"
    assert candidate["request_kind"] == "jtable_call"
    assert candidate["submit_method"] == "ChangeBatchItem"
    assert candidate["jtable_call"]["method"] == "ChangeBatchItem"
    assert candidate["jtable_call"]["args_template"] == ["__O_ID__", "__EXCHANGE_ITEMS_JSON__", "__KEEP_TARGET_INFO__"]


def test_exchange_candidate_allows_unshipped_paid_pending_review_and_abnormal_statuses() -> None:
    candidate = exchange_resend._build_exchange_submit_candidate(
        {
            "action": "exchange",
            "internal_order_id": "10001",
            "online_order_id": "LP001",
            "sku_code": "SKU-1",
            "qty": 1,
        }
    )

    assert candidate["eligible_status"] == ["已发货", "未发货", "已付款待审核", "异常"]


def test_exchange_sku_code_is_target_sku_not_required_in_original_order(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="已付款待审核")

    response = exchange_resend.preview_order_exchange_resend(
        order_no="LP001",
        mode="exchange",
        sku_code="SKU-2",
    )

    assert response.data["eligible"] is True
    assert response.data["sku_matched"] is None
    assert response.data["final_payload"]["sku_code"] == "SKU-1"
    assert response.data["final_payload"]["exchange_sku_code"] == "SKU-2"
    assert response.data["pending_confirmation"] == []
    assert response.data["exchange_submit_candidate"]["defaults"] == {
        "return_sku_code": "SKU-1",
        "exchange_sku_code": "SKU-2",
        "qty": 1,
    }


def test_exchange_candidate_steps_use_picker_modal_for_target_sku() -> None:
    steps = exchange_resend._exchange_candidate_steps()

    assert {
        "stage": "order_item_row",
        "action": "open_exchange_picker",
        "trigger": "row_exchange_button",
    } in steps
    assert {
        "stage": "exchange_picker",
        "action": "search_target_sku",
        "field": "sku_code",
        "sku_placeholder": "__EXCHANGE_SKU__",
    } in steps
    assert {
        "stage": "exchange_picker",
        "action": "select_target_sku",
        "sku_placeholder": "__EXCHANGE_SKU__",
    } in steps
    assert {
        "stage": "exchange_picker",
        "action": "confirm_exchange_picker",
        "method": "ChangeBatchItem",
        "called": False,
    } in steps


def test_learn_exchange_passes_target_sku_to_picker_probe(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="已付款待审核")
    captured: dict[str, Any] = {}

    def fake_explore(
        *,
        order_no: str,
        mode: str,
        screenshot_dir: str | None,
        sku_code: str | None,
        qty: int,
    ) -> dict[str, Any]:
        captured.update(
            {
                "order_no": order_no,
                "mode": mode,
                "screenshot_dir": screenshot_dir,
                "sku_code": sku_code,
                "qty": qty,
            }
        )
        return {
            "steps_detected": [],
            "screenshot_paths": [],
            "profile_path": str(tmp_path / "profile.json"),
            "exchange_draft_probe": {"target_sku_code": sku_code},
        }

    monkeypatch.setattr(exchange_resend, "_explore_exchange_resend_page", fake_explore)

    response = exchange_resend.learn_order_exchange_resend(
        order_no="LP001",
        mode="exchange",
        sku_code="SKU-2",
        qty=1,
    )

    assert captured["sku_code"] == "SKU-2"
    assert response.data["exchange_draft_probe"]["target_sku_code"] == "SKU-2"
    assert response.data["pending_confirmation"] == []


def test_exchange_legacy_template_statuses_merge_default_allowed_statuses(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="异常")
    template_path = tmp_path / "data" / "jst" / "order_exchange_resend_template.json"
    template_path.parent.mkdir(parents=True)
    template_path.write_text(
        json.dumps(
            {
                "confirmed": True,
                "request_kind": "jtable_call",
                "supported_modes": ["resend"],
                "method": "POST",
                "url": "https://www.erp321.com/app/order/order/list.aspx",
                "jtable_call": {
                    "callback_id": "JTable1",
                    "method": "CreateReissueOrderAllItem",
                    "args_template": ["__O_ID__", "false", "false", "false"],
                    "call_control": "{page}",
                },
                "field_map": {},
                "eligible_status": ["已发货"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        exchange_resend,
        "_submit_exchange_api_flow",
        lambda **kwargs: {"submitted": True, "request_kind": "jtable_call"},
    )

    response = exchange_resend.submit_order_exchange_resend(
        order_no="LP001",
        mode="exchange",
        confirm_order_no="LP001",
        execute=True,
    )

    assert response.data["eligible"] is True
    assert response.data["submitted"] is True
    assert response.data["final_payload"]["order_status"] == "异常"


def test_submit_exchange_with_resend_only_template_uses_api_flow(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="已发货")
    template_path = tmp_path / "data" / "jst" / "order_exchange_resend_template.json"
    template_path.parent.mkdir(parents=True)
    template_path.write_text(
        json.dumps(
            {
                "confirmed": True,
                "request_kind": "jtable_call",
                "supported_modes": ["resend"],
                "method": "POST",
                "url": "https://www.erp321.com/app/order/order/list.aspx",
                "jtable_call": {
                    "callback_id": "JTable1",
                    "method": "CreateReissueOrderAllItem",
                    "args_template": ["__O_ID__", "false", "false", "false"],
                    "call_control": "{page}",
                },
                "field_map": {},
                "eligible_status": ["已发货"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        exchange_resend,
        "build_client",
        lambda **kwargs: pytest.fail("exchange submit must not call resend template HTTP path"),
    )
    captured: dict[str, Any] = {}

    def fake_api_flow(*, final_payload: dict[str, Any], order_no: str, resolved: dict[str, Any]) -> dict[str, Any]:
        captured.update({"final_payload": final_payload, "order_no": order_no, "resolved": resolved})
        return {"submitted": True, "request_kind": "jtable_call", "submit_method": "ChangeBatchItem"}

    monkeypatch.setattr(exchange_resend, "_submit_exchange_api_flow", fake_api_flow)
    monkeypatch.setattr(
        exchange_resend,
        "_submit_exchange_browser_flow",
        lambda **kwargs: pytest.fail("exchange submit must not use browser flow"),
    )

    response = exchange_resend.submit_order_exchange_resend(
        order_no="LP001",
        mode="exchange",
        confirm_order_no="LP001",
        execute=True,
    )

    assert captured["order_no"] == "LP001"
    assert response.data["submitted"] is True
    assert response.data["pending_confirmation"] == []
    assert response.data["final_payload"]["sku_code"] == "SKU-1"
    assert response.data["exchange_submit_candidate"]["dry_run_only"] is False


def test_submit_exchange_without_template_runs_api_flow(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="已付款待审核")
    captured: dict[str, Any] = {}

    def fake_api_flow(*, final_payload: dict[str, Any], order_no: str, resolved: dict[str, Any]) -> dict[str, Any]:
        captured.update({"final_payload": final_payload, "order_no": order_no, "resolved": resolved})
        return {
            "submitted": True,
            "request_kind": "jtable_call",
            "submit_method": "ChangeBatchItem",
            "before_sku_code": "SKU-1",
            "after_sku_code": "SKU-2",
        }

    monkeypatch.setattr(exchange_resend, "_submit_exchange_api_flow", fake_api_flow)
    monkeypatch.setattr(
        exchange_resend,
        "_submit_exchange_browser_flow",
        lambda **kwargs: pytest.fail("exchange submit must not use browser flow"),
    )

    response = exchange_resend.submit_order_exchange_resend(
        order_no="LP001",
        mode="exchange",
        confirm_order_no="LP001",
        sku_code="SKU-2",
        execute=True,
    )

    assert captured["order_no"] == "LP001"
    assert captured["final_payload"]["sku_code"] == "SKU-1"
    assert captured["final_payload"]["exchange_sku_code"] == "SKU-2"
    assert response.data["submitted"] is True
    assert response.data["result"]["request_kind"] == "jtable_call"
    assert response.data["pending_confirmation"] == []


def test_build_exchange_items_payload_matches_jst_change_batch_item_shape() -> None:
    source_item = {
        "sku_id": "SKU-1",
        "qty": 1,
        "price": 998.0,
        "oi_id": "OI-1",
        "is_gift": False,
        "il_id": "",
        "sku_type": "normal",
        "remark": "old",
    }
    other_item = {
        "sku_id": "SKU-KEEP",
        "qty": 2,
        "price": 10,
        "oi_id": "OI-2",
        "is_gift": True,
        "il_id": "IL-2",
        "sku_type": "normal",
        "remark": "",
    }
    target_sku = {
        "sku_id": "SKU-2",
        "qty": 59,
        "sale_price": 888.0,
        "sku_type": "normal",
    }

    payload = exchange_resend._build_exchange_items_payload(
        source_oi_id="OI-1",
        order_items=[source_item, other_item],
        target_skus=[target_sku],
        keep_target_info=True,
    )

    assert payload == {
        "items": [
            {
                "sku_id": "SKU-1",
                "qty": 1,
                "price": 998.0,
                "amount": "998.00",
                "is_gift": False,
                "oi_id": "OI-1",
                "is_del": True,
                "il_id": "",
                "sku_type": "normal",
                "is_new": False,
                "remark": "old",
            },
            {
                "sku_id": "SKU-2",
                "qty": 1,
                "price": 888.0,
                "amount": "888.00",
                "is_gift": False,
                "oi_id": 0,
                "is_del": False,
                "il_id": None,
                "sku_type": "normal",
                "is_new": True,
                "remark": "",
            },
            {
                "sku_id": "SKU-KEEP",
                "qty": 2,
                "price": 10.0,
                "amount": "20.00",
                "is_gift": True,
                "oi_id": "OI-2",
                "is_del": False,
                "il_id": "IL-2",
                "sku_type": "normal",
                "is_new": False,
                "remark": "",
            },
        ]
    }


def test_submit_with_confirmed_jtable_template_posts_callpage_form(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="已发货")
    template_path = tmp_path / "data" / "jst" / "order_exchange_resend_template.json"
    template_path.parent.mkdir(parents=True)
    template_path.write_text(
        json.dumps(
            {
                "confirmed": True,
                "request_kind": "jtable_call",
                "method": "POST",
                "url": "https://www.erp321.com/app/order/order/list.aspx",
                "post_data_template": {
                    "__CALLBACKID": "JTable1",
                    "__CALLBACKPARAM": {
                        "Method": "CreateReissueOrderAllItem",
                        "Args": ["__O_ID__", "false", "false", "false"],
                        "CallControl": "{page}",
                    },
                },
                "jtable_call": {
                    "callback_id": "JTable1",
                    "method": "CreateReissueOrderAllItem",
                    "args_template": ["__O_ID__", "false", "false", "false"],
                    "call_control": "{page}",
                },
                "field_map": {"internal_order_id": "__O_ID__", "sku_code": "__SKU__", "qty": "__QTY__"},
                "eligible_status": ["已发货"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured_posts: list[dict[str, Any]] = []

    class FakeResponse:
        text = '0|{"IsSuccess":true,"ReturnValue":"补发订单创建成功"}'

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            captured_posts.append({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(
        exchange_resend,
        "_load_order_session",
        lambda: (
            {"headers": {"User-Agent": "pytest"}},
            "sid=abc",
            "https://www.erp321.com/app/order/order/list.aspx",
            {"__VIEWSTATE": "view-state"},
        ),
    )
    monkeypatch.setattr(exchange_resend, "build_client", lambda **kwargs: FakeClient())

    response = exchange_resend.submit_order_exchange_resend(
        order_no="LP001",
        mode="resend",
        confirm_order_no="LP001",
        execute=True,
    )

    assert response.data["submitted"] is True
    assert response.data["result"]["return_value"] == "补发订单创建成功"
    assert len(captured_posts) == 1
    posted = captured_posts[0]
    callback_param = json.loads(posted["data"]["__CALLBACKPARAM"])
    assert posted["url"] == "https://www.erp321.com/app/order/order/list.aspx"
    assert posted["data"]["__CALLBACKID"] == "JTable1"
    assert posted["data"]["am___"] == "CreateReissueOrderAllItem"
    assert posted["data"]["__VIEWSTATE"] == "view-state"
    assert callback_param == {
        "Method": "CreateReissueOrderAllItem",
        "Args": ["10001", "false", "false", "false"],
        "CallControl": "{page}",
    }


def test_jtable_prompt_failure_is_not_treated_as_submitted(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="已发货")
    template_path = tmp_path / "data" / "jst" / "order_exchange_resend_template.json"
    template_path.parent.mkdir(parents=True)
    template_path.write_text(
        json.dumps(
            {
                "confirmed": True,
                "request_kind": "jtable_call",
                "method": "POST",
                "url": "https://www.erp321.com/app/order/order/list.aspx",
                "jtable_call": {
                    "callback_id": "JTable1",
                    "method": "CreateReissueOrderAllItem",
                    "args_template": ["__O_ID__", "false", "false", "false"],
                    "call_control": "{page}",
                    "force_retry_on_prompt": False,
                },
                "field_map": {},
                "eligible_status": ["已发货"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        text = (
            '0|{"IsSuccess":true,'
            '"ReturnValue":"创建成功 0 条，失败 1 条。原因如下：\\r\\n10001：提示:SKU-1 已经补发过了，确定要创建吗?\\r\\n"}'
        )

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(
        exchange_resend,
        "_load_order_session",
        lambda: ({}, "sid=abc", "https://www.erp321.com/app/order/order/list.aspx", {}),
    )
    monkeypatch.setattr(exchange_resend, "build_client", lambda **kwargs: FakeClient())

    with pytest.raises(RuntimeError, match="二次确认提示"):
        exchange_resend.submit_order_exchange_resend(
            order_no="LP001",
            mode="resend",
            confirm_order_no="LP001",
            execute=True,
        )


_WHOLE_ORDER_TEMPLATE = {
    "confirmed": True,
    "request_kind": "jtable_call",
    "supported_modes": ["resend"],
    "method": "POST",
    "url": "https://www.erp321.com/app/order/order/list.aspx",
    "jtable_call": {
        "callback_id": "JTable1",
        "method": "CreateReissueOrderAllItem",
        "args_template": ["__O_ID__", "false", "false", "false"],
        "call_control": "{page}",
    },
    "eligible_status": ["已发货"],
}

_ITEM_SELECTION_TEMPLATE = {
    "confirmed": True,
    "supported_modes": ["resend"],
    "method": "POST",
    "url": "https://api.example.com/order/exchange-resend",
    "post_data_template": {"sku": "__SKU__", "qty": "__QTY__"},
    "eligible_status": ["已发货"],
}


def _write_template(tmp_path: Path, payload: dict[str, Any]) -> None:
    template_path = tmp_path / "data" / "jst" / "order_exchange_resend_template.json"
    template_path.parent.mkdir(parents=True, exist_ok=True)
    template_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_partial_resend_conflict_blocks_whole_order_with_item_params() -> None:
    assert exchange_resend._partial_resend_conflict("resend", _WHOLE_ORDER_TEMPLATE, "SKU-1", 1)
    assert exchange_resend._partial_resend_conflict("resend", _WHOLE_ORDER_TEMPLATE, None, 2)
    # 整单补发默认参数（无 sku、qty=1）不拦截
    assert exchange_resend._partial_resend_conflict("resend", _WHOLE_ORDER_TEMPLATE, None, 1) is None
    # 按 SKU 模板会真正用到 sku/qty，不拦截
    assert exchange_resend._partial_resend_conflict("resend", _ITEM_SELECTION_TEMPLATE, "SKU-1", 2) is None
    # 无模板 / 换货模式不拦截
    assert exchange_resend._partial_resend_conflict("resend", None, "SKU-1", 2) is None
    assert exchange_resend._partial_resend_conflict("exchange", _WHOLE_ORDER_TEMPLATE, "SKU-1", 2) is None


def test_submit_resend_rejects_explicit_sku_for_whole_order(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="已发货")
    _write_template(tmp_path, _WHOLE_ORDER_TEMPLATE)

    def _boom_session() -> Any:  # 提交前应直接拦截，绝不打平台
        raise AssertionError("整单补发误传 sku 时不应触发任何平台调用")

    monkeypatch.setattr(exchange_resend, "_load_order_session", _boom_session)

    with pytest.raises(RuntimeError, match="整单补发"):
        exchange_resend.submit_order_exchange_resend(
            order_no="LP001",
            mode="resend",
            confirm_order_no="LP001",
            sku_code="SKU-1",
            execute=True,
        )


def test_submit_resend_rejects_qty_for_whole_order(monkeypatch, tmp_path: Path) -> None:
    _patch_successful_order(monkeypatch, tmp_path, status="已发货")
    _write_template(tmp_path, _WHOLE_ORDER_TEMPLATE)

    with pytest.raises(RuntimeError, match="整单补发"):
        exchange_resend.submit_order_exchange_resend(
            order_no="LP001",
            mode="resend",
            confirm_order_no="LP001",
            qty=3,
            execute=True,
        )
