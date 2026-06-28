from ops_cli.platforms.jst import order


def test_run_order_label_retries_after_auth_refresh(monkeypatch) -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.capture_calls = 0

        def ensure_scene(self, site, scene):
            return {"headers": {"cookie": "a=b"}, "url": "https://www.erp321.com/app/order/order/list.aspx"}

        def capture_scene(self, site, scene):
            self.capture_calls += 1
            return {"headers": {"cookie": "a=b"}, "url": "https://www.erp321.com/app/order/order/list.aspx"}

    manager = FakeManager()
    monkeypatch.setattr(order, "get_scene_manager", lambda: manager)
    monkeypatch.setattr(order, "_normalize_orders", lambda **kwargs: (["TB1"], None))
    monkeypatch.setattr(order, "_write_failed_orders", lambda results, prefix="jst_tag_failed_orders": None)

    calls = {"count": 0}

    def fake_query(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise order.httpx.HTTPStatusError(
                "401 Unauthorized",
                request=order.httpx.Request("POST", "https://www.erp321.com"),
                response=order.httpx.Response(401),
            )
        return ["OID1"]

    monkeypatch.setattr(order, "_query_order_o_ids", fake_query)
    monkeypatch.setattr(order, "_append_remark", lambda *args, **kwargs: None)
    monkeypatch.setattr(order, "_set_labels", lambda *args, **kwargs: None)

    response = order.run_order_label(
        order_ids=["TB1"],
        input_path=None,
        limit=None,
        execute=False,
        labels=order.DEFAULT_LABELS,
        remark_text=order.DEFAULT_REMARK_TEXT,
    )

    assert response.data["summary"]["success"] == 1
    assert response.data["auth_refresh_applied"] is True
    assert manager.capture_calls == 1
