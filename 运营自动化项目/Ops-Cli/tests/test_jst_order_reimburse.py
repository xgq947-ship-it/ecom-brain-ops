from decimal import Decimal
from pathlib import Path

from ops_cli.platforms.jst import reimburse


def test_build_reimburse_payload_defaults() -> None:
    payload = reimburse.build_reimburse_workorder_payload(
        internal_order_id="10001",
        online_order_id="LP10001",
        principal_total=Decimal("965"),
        payout_total=Decimal("140"),
        product_code="SUZBHLYZHH1001",
        item_name="智能足部护理仪",
        upload_url="https://oss.example.com/reimburse.xlsx",
    )

    data = payload["data"]
    assert data["title"] == "运营特殊单报销打款"
    assert data["woTypeId"] == "48DF537274074D87B7BBB8A7EEAD6B21"
    assert data["orderIds"] == ["10001"]
    assert {"fieldId": "TEXTField1023127320230801180658", "value": "6212261407005274259+肖国清"} in data["businessField"]
    assert {"fieldId": "NUMBERField1023127320230729214743", "value": 965.0} in data["businessField"]
    assert {"fieldId": "amountField", "value": 140.0} in data["businessField"]
    assert {"fieldId": "FILE_UPLOADField1023127320230728164848", "value": ["https://oss.example.com/reimburse.xlsx"]} in data["businessField"]
    assert {"fieldId": "alipayAccountField", "value": "15659384388"} in data["businessField"]
    assert {"fieldId": "alipayNameField", "value": "Evan"} in data["businessField"]


def test_run_reimburse_dry_run_checks_existing_without_upload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    workbook = tmp_path / "登记表.xlsx"
    workbook.write_bytes(b"fake")

    class FakeManager:
        def ensure_scene(self, site, scene):
            return {"headers": {"cookie": "u_id=1; u_co_id=2"}, "url": "https://www.erp321.com/app/order/order/list.aspx"}

        def capture_scene(self, site, scene):
            return {"headers": {"cookie": "u_id=1; u_co_id=2"}, "url": "https://www.erp321.com/app/order/order/list.aspx"}

    monkeypatch.setattr(reimburse, "get_scene_manager", lambda: FakeManager())

    monkeypatch.setattr(
        reimburse,
        "_resolve_order_identity",
        lambda outer_order_id: {
            "outer_order_id": outer_order_id,
            "matched_filter": "outer_so_id",
            "internal_order_id": "10001",
            "online_order_id": "LP10001",
            "item_name": "智能足部护理仪",
        },
    )
    monkeypatch.setattr(reimburse, "_select_created_workorder", lambda *args, **kwargs: {})

    def fail_upload(*args, **kwargs):
        raise AssertionError("dry-run must not upload")

    monkeypatch.setattr(reimburse, "_get_sts_token", fail_upload)
    monkeypatch.setattr(reimburse, "_upload_workbook_to_oss", fail_upload)
    monkeypatch.setattr(reimburse, "_post_workorder", fail_upload)

    response = reimburse.run_order_reimburse_workorder(
        outer_order_id="TB123",
        principal_total="965",
        payout_total="140",
        product_code="SUZBHLYZHH1001",
        product_name="登记表商品名",
        workbook_file=str(workbook),
        execute=False,
    )

    assert response.success is True
    assert response.command == "order reimburse"
    assert response.data["submitted"] is False
    assert response.data["has_existing_workorder"] is False
    assert response.data["internal_order_id"] == "10001"
    assert response.data["online_order_id"] == "LP10001"
    assert Path(response.data["context_path"]).exists()


def test_run_reimburse_retries_after_auth_refresh(monkeypatch, tmp_path: Path) -> None:
    workbook = tmp_path / "登记表.xlsx"
    workbook.write_bytes(b"fake")

    class FakeManager:
        def __init__(self) -> None:
            self.capture_calls = 0

        def ensure_scene(self, site, scene):
            return {"headers": {"cookie": "u_id=1; u_co_id=2"}, "url": "https://www.erp321.com/app/order/order/list.aspx"}

        def capture_scene(self, site, scene):
            self.capture_calls += 1
            return {"headers": {"cookie": "u_id=1; u_co_id=2"}, "url": "https://www.erp321.com/app/order/order/list.aspx"}

    manager = FakeManager()
    monkeypatch.setattr(reimburse, "get_scene_manager", lambda: manager)

    attempts = {"count": 0}

    def fake_resolve(outer_order_id):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("401 Unauthorized")
        return {
            "outer_order_id": outer_order_id,
            "matched_filter": "outer_so_id",
            "internal_order_id": "10001",
            "online_order_id": "LP10001",
            "item_name": "智能足部护理仪",
        }

    monkeypatch.setattr(reimburse, "_resolve_order_identity", fake_resolve)
    monkeypatch.setattr(reimburse, "_select_created_workorder", lambda *args, **kwargs: {})

    response = reimburse.run_order_reimburse_workorder(
        outer_order_id="TB123",
        principal_total="965",
        payout_total="140",
        product_code="SUZBHLYZHH1001",
        product_name="登记表商品名",
        workbook_file=str(workbook),
        execute=False,
    )

    assert response.data["submitted"] is False
    assert response.data["auth_refresh_applied"] is True
    assert manager.capture_calls == 1
