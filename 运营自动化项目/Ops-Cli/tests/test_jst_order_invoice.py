from decimal import Decimal
from pathlib import Path

from ops_cli.platforms.jst import invoice


def test_build_invoice_payload_defaults() -> None:
    payload = invoice.build_invoice_workorder_payload(
        order_id="TB123",
        internal_order_id="10001",
        online_order_id="LP10001",
        invoice_type="专用发票",
        shop_name="奥克斯索隆专卖店",
        invoice_entity="福安市索隆电子有限公司",
        title="YOUR_ACCOUNT",
        tax_no="91330000TEST",
        address="YOUR_ACCOUNT",
        phone="YOUR_ACCOUNT",
        bank="YOUR_ACCOUNT",
        bank_account="YOUR_ACCOUNT",
        amount=Decimal("128.50"),
        quantity=1,
    )

    data = payload["data"]
    assert data["title"] == "发票"
    assert data["orderIds"] == ["10001"]
    assert {"fieldId": "SELECTField1023127320230406160017", "value": "专用发票"} in data["businessField"]
    assert {"fieldId": "shopField", "value": "奥克斯索隆专卖店"} in data["businessField"]
    assert {"fieldId": "SELECTField1023127320240724093511", "value": "福安市索隆电子有限公司"} in data["businessField"]
    assert {"fieldId": "actualPaidAmountField", "value": 128.5} in data["businessField"]
    assert {"fieldId": "itemsCount", "value": 1} in data["businessField"]
    assert data["woTypeId"] == "5821A58E9D39459DBC4E87569A9A6D68"


def test_write_template_uses_discovered_workorder_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    template_path = invoice._write_template(  # noqa: SLF001
        scene_data={"status": "captured"},
        workorder_type_id="REAL_WO_TYPE_ID",
        field_map={
            "invoice_type": "REAL_INVOICE_TYPE",
            "shop_name": "REAL_SHOP",
        },
    )

    template = invoice._read_json(template_path)  # noqa: SLF001
    assert template["workorder_type_id"] == "REAL_WO_TYPE_ID"
    assert template["field_map"]["invoice_type"] == "REAL_INVOICE_TYPE"
    assert template["field_map"]["shop_name"] == "REAL_SHOP"


def test_run_invoice_workorder_dry_run_writes_context(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    def fake_resolve_order_identity(order_id: str, outer_order_id: str | None = None) -> dict[str, str]:
        return {"order_id": order_id, "internal_order_id": "10001", "online_order_id": "LP10001", "matched_filter": "outer_so_id"}

    monkeypatch.setattr(invoice, "_resolve_order_identity", fake_resolve_order_identity)

    response = invoice.run_order_invoice_workorder(
        order_id="TB123",
        outer_order_id=None,
        invoice_type="专用发票",
        shop_name="奥克斯索隆专卖店",
        invoice_entity="福安市索隆电子有限公司",
        title="YOUR_ACCOUNT",
        tax_no="91330000TEST",
        address="YOUR_ACCOUNT",
        phone="YOUR_ACCOUNT",
        bank="YOUR_ACCOUNT",
        bank_account="YOUR_ACCOUNT",
        amount="128.50",
        quantity=1,
        execute=False,
    )

    assert response.success is True
    assert response.command == "order invoice"
    assert response.data["submitted"] is False
    assert response.data["mode"] == "dry-run"
    assert response.data["payload"]["data"]["orderIds"] == ["10001"]
    assert Path(response.data["context_path"]).exists()


def test_learn_order_invoice_workorder_discovers_type_and_fields(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    class FakeManager:
        def ensure_scene(self, site: str, scene: str) -> dict[str, str]:
            return {"site": site, "scene": scene}

        def capture_scene(self, site: str, scene: str) -> dict[str, str]:
            return {"site": site, "scene": scene, "status": "captured"}

    monkeypatch.setattr(invoice, "get_scene_manager", lambda: FakeManager())
    monkeypatch.setattr(
        invoice,
        "_discover_workorder_template_config",
        lambda: {
            "workorder_type_id": "REAL_WO_TYPE_ID",
            "field_map": {"invoice_type": "REAL_INVOICE_TYPE", "shop_name": "REAL_SHOP"},
        },
    )

    response = invoice.learn_order_invoice_workorder(force=True)

    assert response.success is True
    template = invoice._read_json(Path(response.data["template_path"]))  # noqa: SLF001
    assert template["workorder_type_id"] == "REAL_WO_TYPE_ID"
    assert template["field_map"]["shop_name"] == "REAL_SHOP"


def test_resolve_order_identity_passes_form_template_by_keyword(monkeypatch) -> None:
    fake_session = {
        "headers": {"Cookie": "u_id=1; u_co_id=2"},
        "url": "https://www.erp321.com/app/order/order/list.aspx",
    }

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeManager:
        def ensure_scene(self, site: str, scene: str) -> dict[str, str]:
            return fake_session

    captured: dict[str, object] = {}

    def fake_query(client, url, cookie, order_id, outer_order_id, identifier=None, form_template=None):
        captured["identifier"] = identifier
        captured["form_template"] = form_template
        return ([{"o_id": "10001", "so_id": "LP10001"}], "so_id")

    monkeypatch.setattr(invoice, "get_scene_manager", lambda: FakeManager())
    monkeypatch.setattr(invoice, "_extract_form_template", lambda session: {"token": "x"})
    monkeypatch.setattr(invoice, "_query_order_rows_by_identifier", fake_query)
    monkeypatch.setattr(invoice, "build_client", lambda **kwargs: FakeClient())

    result = invoice._resolve_order_identity("TB123")  # noqa: SLF001

    assert captured["identifier"] is None
    assert captured["form_template"] == {"token": "x"}
    assert result["internal_order_id"] == "10001"
