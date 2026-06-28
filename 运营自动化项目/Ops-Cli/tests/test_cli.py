import json

import pytest
from typer.testing import CliRunner

from ops_cli.capabilities import current_capability_execution
from ops_cli.cli import app
from ops_cli.output import CommandResponse


runner = CliRunner()


def test_ops_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Usage" in result.stdout
    assert "jst" in result.stdout
    assert "tmcs" in result.stdout


def test_jst_profit_yesterday_json(monkeypatch) -> None:
    def fake_run_yesterday_profit(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="profit yesterday",
            data={"date": "2026-05-15", "profit": 929.8, "metric_field": "经营利润"},
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_yesterday_profit", fake_run_yesterday_profit)

    result = runner.invoke(app, ["--json", "jst", "profit", "yesterday"])

    assert result.exit_code == 0
    assert '"success": true' in result.stdout
    assert '"platform": "jst"' in result.stdout
    assert '"command": "profit yesterday"' in result.stdout


def test_jst_profit_yesterday_detail_json(monkeypatch) -> None:
    seen_kwargs = {}

    def fake_run_yesterday_profit(**kwargs) -> CommandResponse:
        seen_kwargs.update(kwargs)
        return CommandResponse(
            success=True,
            platform="jst",
            command="profit yesterday",
            data={
                "date": "2026-05-15",
                "profit": 929.8,
                "metric_field": "经营利润",
                "metrics": [{"name": "销售收入", "value": 1234.56}],
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_yesterday_profit", fake_run_yesterday_profit)

    result = runner.invoke(app, ["--json", "jst", "profit", "yesterday", "--detail"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert seen_kwargs["detail"] is True
    assert payload["data"]["metrics"] == [{"name": "销售收入", "value": 1234.56}]


def test_tmcs_product_list_json() -> None:
    result = runner.invoke(app, ["--json", "tmcs", "product", "list"])

    assert result.exit_code == 0
    assert '"success": true' in result.stdout
    assert '"platform": "tmcs"' in result.stdout
    assert '"command": "product list"' in result.stdout


def test_tmcs_product_help() -> None:
    result = runner.invoke(app, ["tmcs", "product", "--help"])

    assert result.exit_code == 0
    assert "sync" in result.stdout
    assert "learn" in result.stdout


def test_tmcs_inventory_help() -> None:
    result = runner.invoke(app, ["tmcs", "inventory", "--help"])

    assert result.exit_code == 0
    assert "export" in result.stdout
    assert "learn" in result.stdout
    assert "adjust" in result.stdout


def test_tmcs_stock_query_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "ops_cli.platforms.tmcs.platform.query_stock",
        lambda item_ids, warehouse_code: [
            {
                "platform_item_id": "1052534376394",
                "platform_sku_id": "6247519890565",
                "supplier_goods_id": "G-001",
                "merchant_goods_code": "AOK-SKU-001",
            }
        ],
    )

    result = runner.invoke(
        app,
        [
            "tmcs",
            "stock",
            "query",
            "--item-ids",
            "1052534376394",
            "--warehouse-code",
            "mc_aokesi_suolong",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["command"] == "stock query"
    assert payload["data"]["rows"][0]["platform_item_id"] == "1052534376394"
    assert payload["data"]["capability_id"] == "tmcs.stock.query"
    assert payload["data"]["session_recovery"]["retry_count"] == 0


def test_browser_check_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "ops_cli.cli.check_browser_port",
        lambda port: CommandResponse(
            success=True,
            platform="browser",
            command="check",
            data={"port": port, "available": True},
        ),
    )

    result = runner.invoke(app, ["--json", "browser", "check", "--port", "9222"])

    assert result.exit_code == 0
    assert '"command": "check"' in result.stdout
    assert '"port": 9222' in result.stdout


def test_browser_tabs_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "ops_cli.cli.list_browser_tabs",
        lambda port: CommandResponse(
            success=True,
            platform="browser",
            command="tabs",
            data={"port": port, "page_count": 1, "tabs": [{"url": "about:blank"}]},
        ),
    )

    result = runner.invoke(app, ["--json", "browser", "tabs", "--port", "9222"])

    assert result.exit_code == 0
    assert '"command": "tabs"' in result.stdout
    assert '"page_count": 1' in result.stdout


def test_browser_cleanup_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "ops_cli.cli.cleanup_browser_tabs",
        lambda port, dry_run: CommandResponse(
            success=True,
            platform="browser",
            command="cleanup",
            data={"port": port, "dry_run": dry_run, "close_count": 2},
        ),
    )

    result = runner.invoke(app, ["--json", "browser", "cleanup", "--port", "9222", "--dry-run"])

    assert result.exit_code == 0
    assert '"command": "cleanup"' in result.stdout
    assert '"dry_run": true' in result.stdout
    assert '"close_count": 2' in result.stdout


def test_jst_browser_learn_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "ops_cli.platforms.jst.platform.learn_jst_browser_scene",
        lambda scene, timeout, cdp_url: CommandResponse(
            success=True,
            platform="jst",
            command="browser learn",
            data={"scene": scene, "profile_path": "runtime/browser/jst_shop-goods-import.json"},
        ),
    )

    result = runner.invoke(app, ["--json", "jst", "browser", "learn", "--scene", "shop-goods-import"])

    assert result.exit_code == 0
    assert '"scene": "shop-goods-import"' in result.stdout


def test_jst_shop_goods_import_json(monkeypatch, tmp_path) -> None:
    excel_path = tmp_path / "import.xlsx"
    excel_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "ops_cli.platforms.jst.platform.import_jst_shop_goods",
        lambda file_path, shop_name, mode: CommandResponse(
            success=True,
            platform="jst",
            command="shop-goods import",
            data={"file": str(file_path), "shop_name": shop_name, "mode": mode, "status": "submitted"},
        ),
    )

    result = runner.invoke(
        app,
        [
            "--json",
            "jst",
            "shop-goods",
            "import",
            "--file",
            str(excel_path),
            "--shop-name",
            "（猫超）启明工贸有限公司",
            "--mode",
            "cover",
        ],
    )

    assert result.exit_code == 0
    assert '"command": "shop-goods import"' in result.stdout
    assert '"status": "submitted"' in result.stdout
    assert '"mode": "cover"' in result.stdout


def test_tmcs_product_sync_json(monkeypatch) -> None:
    def fake_run_tmcs_product_sync(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="product sync",
            data={"new_rows": 3, "output_path": "~/Desktop/电商Brain/02-运营店铺/主数据/猫超商品列表导出 (最新）.xlsx"},
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.run_product_sync", fake_run_tmcs_product_sync)

    result = runner.invoke(app, ["--json", "tmcs", "product", "sync", "--dry-run"])

    assert result.exit_code == 0
    assert '"command": "product sync"' in result.stdout
    assert '"new_rows": 3' in result.stdout


def test_tmcs_product_learn_json(monkeypatch) -> None:
    def fake_learn_tmcs_product_sync(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="product learn",
            data={"export_scene": "maochao_item_export", "next_command": "ops --json tmcs product sync"},
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.learn_product_sync", fake_learn_tmcs_product_sync)

    result = runner.invoke(app, ["--json", "tmcs", "product", "learn"])

    assert result.exit_code == 0
    assert '"command": "product learn"' in result.stdout
    assert '"maochao_item_export"' in result.stdout


def test_tmcs_inventory_export_json(monkeypatch) -> None:
    def fake_run_tmcs_inventory_export(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="inventory export",
            data={
                "warehouse_code": kwargs["warehouse_code"],
                "downloaded": False,
                "output_path": "~/Downloads/猫超商品库存列表导出.xlsx",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.run_inventory_export", fake_run_tmcs_inventory_export)

    result = runner.invoke(app, ["--json", "tmcs", "inventory", "export", "--dry-run"])

    assert result.exit_code == 0
    assert '"command": "inventory export"' in result.stdout
    assert '"warehouse_code": "mc_aokesi_suolong"' in result.stdout


def test_tmcs_inventory_learn_json(monkeypatch) -> None:
    def fake_learn_tmcs_inventory_export(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="inventory learn",
            data={
                "inventory_search_scene": "maochao_inventory_search",
                "inventory_export_scene": "maochao_inventory_export",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.learn_inventory_export", fake_learn_tmcs_inventory_export)

    result = runner.invoke(app, ["--json", "tmcs", "inventory", "learn"])

    assert result.exit_code == 0
    assert '"command": "inventory learn"' in result.stdout
    assert '"maochao_inventory_export"' in result.stdout


def test_tmcs_inventory_adjust_json(monkeypatch) -> None:
    def fake_run_tmcs_inventory_adjust(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="inventory adjust",
            data={
                "action": kwargs["action"],
                "submitted": kwargs["execute"],
                "adjusted_count": len(kwargs["sku_adjust"]),
            },
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.run_inventory_adjust", fake_run_tmcs_inventory_adjust)

    result = runner.invoke(
        app,
        ["--json", "tmcs", "inventory", "adjust", "--action", "increase", "--sku-adjust", "6247519890565:50"],
    )

    assert result.exit_code == 0
    assert '"command": "inventory adjust"' in result.stdout
    assert '"adjusted_count": 1' in result.stdout


def test_tmcs_inventory_adjust_learn_json(monkeypatch) -> None:
    def fake_learn_tmcs_inventory_adjust(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="inventory adjust learn",
            data={"inventory_adjust_scene": "maochao_inventory_adjust"},
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.learn_inventory_adjust", fake_learn_tmcs_inventory_adjust)

    result = runner.invoke(app, ["--json", "tmcs", "inventory", "adjust-learn"])

    assert result.exit_code == 0
    assert '"command": "inventory adjust learn"' in result.stdout
    assert '"maochao_inventory_adjust"' in result.stdout


def test_tmcs_bill_help() -> None:
    result = runner.invoke(app, ["tmcs", "bill", "--help"])

    assert result.exit_code == 0
    assert "download" in result.stdout
    assert "learn" in result.stdout


def test_tmcs_bill_download_json(monkeypatch) -> None:
    def fake_run_tmcs_bill_download(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="bill download",
            data={"bill_count": 2, "downloaded_files": ["~/Downloads/HDB1.xlsx"]},
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.run_bill_download", fake_run_tmcs_bill_download)

    result = runner.invoke(app, ["--json", "tmcs", "bill", "download", "--last-month"])

    assert result.exit_code == 0
    assert '"command": "bill download"' in result.stdout
    assert '"bill_count": 2' in result.stdout


def test_tmcs_bill_failure_contains_structured_capability_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**kwargs) -> CommandResponse:
        raise RuntimeError("未找到猫超账单下载模板")

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.run_bill_download", fail)

    result = runner.invoke(app, ["--json", "tmcs", "bill", "download", "--dry-run"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["success"] is False
    assert payload["data"]["error_code"] == "TEMPLATE_MISSING"
    assert payload["data"]["retryable"] is False
    assert payload["data"]["required_scenes"]
    assert "recovery_hint" in payload["data"]


def test_auth_failure_is_not_misclassified_by_capture_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> CommandResponse:
        raise RuntimeError(
            "tmall_chaoshi/maochao_item_search session 不可用：接口返回 401；"
            "业务命令会按需捕获对应 scene"
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.ensure_auth", fail)

    result = runner.invoke(app, ["--json", "--no-interactive-login", "tmcs", "auth", "ensure"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["data"]["error_code"] == "AUTH_REQUIRED"


def test_no_interactive_login_disables_recovery_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, bool] = {}

    def fake_run_tmcs_bill_download(**kwargs) -> CommandResponse:
        execution = current_capability_execution()
        assert execution is not None
        observed["allow_recovery"] = execution.allow_recovery
        return CommandResponse(success=True, platform="tmcs", command="bill download", data={})

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.run_bill_download", fake_run_tmcs_bill_download)

    result = runner.invoke(app, ["--json", "--no-interactive-login", "tmcs", "bill", "download"])

    assert result.exit_code == 0
    assert observed == {"allow_recovery": False}


def test_interactive_login_enables_recovery_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, bool] = {}

    def fake_run_tmcs_bill_download(**kwargs) -> CommandResponse:
        execution = current_capability_execution()
        assert execution is not None
        observed["allow_recovery"] = execution.allow_recovery
        return CommandResponse(success=True, platform="tmcs", command="bill download", data={})

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.run_bill_download", fake_run_tmcs_bill_download)

    result = runner.invoke(app, ["--json", "--interactive-login", "tmcs", "bill", "download"])

    assert result.exit_code == 0
    assert observed == {"allow_recovery": True}


def test_tmcs_bill_learn_json(monkeypatch) -> None:
    def fake_learn_tmcs_bill_download(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="bill learn",
            data={"statement_export_scene": "statement_bill_dynamic_list", "next_command": "ops --json tmcs bill download --last-month"},
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.learn_bill_download", fake_learn_tmcs_bill_download)

    result = runner.invoke(app, ["--json", "tmcs", "bill", "learn"])

    assert result.exit_code == 0
    assert '"command": "bill learn"' in result.stdout
    assert '"statement_bill_dynamic_list"' in result.stdout


def test_tmcs_promotion_bill_help() -> None:
    result = runner.invoke(app, ["tmcs", "promotion-bill", "--help"])

    assert result.exit_code == 0
    assert "download" in result.stdout
    assert "learn" in result.stdout


def test_tmcs_promotion_bill_download_json(monkeypatch) -> None:
    def fake_run_tmcs_promotion_bill_download(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="promotion-bill download",
            data={"sources": [{"source": kwargs["source"]}], "downloaded_files": [], "failed": [], "dry_run": kwargs["dry_run"]},
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.run_promotion_bill_download", fake_run_tmcs_promotion_bill_download)

    result = runner.invoke(app, ["--json", "tmcs", "promotion-bill", "download", "--source", "zdx", "--last-month", "--dry-run"])

    assert result.exit_code == 0
    assert '"command": "promotion-bill download"' in result.stdout
    assert '"source": "zdx"' in result.stdout
    assert '"dry_run": true' in result.stdout


def test_tmcs_promotion_bill_learn_json(monkeypatch) -> None:
    def fake_learn_tmcs_promotion_bill(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="promotion-bill learn",
            data={"source": kwargs["source"], "template_path": "data/tmcs/promotion_bill_template.json"},
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.learn_promotion_bill", fake_learn_tmcs_promotion_bill)

    result = runner.invoke(app, ["--json", "tmcs", "promotion-bill", "learn", "--source", "all"])

    assert result.exit_code == 0
    assert '"command": "promotion-bill learn"' in result.stdout
    assert '"source": "all"' in result.stdout


def test_jst_order_label_json(monkeypatch) -> None:
    def fake_run_order_label(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order label",
            data={
                "mode": "dry-run",
                "results": [{"order_no": kwargs["order_ids"][0], "status": "success", "o_id": "123"}],
                "labels": kwargs["labels"],
                "remark_text": kwargs["remark_text"],
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_label", fake_run_order_label)

    result = runner.invoke(app, ["--json", "jst", "order", "label", "--order-id", "123456"])

    assert result.exit_code == 0
    assert '"command": "order label"' in result.stdout
    assert '"status": "success"' in result.stdout
    assert '"123456"' in result.stdout


def test_jst_order_label_json_error(monkeypatch) -> None:
    def fake_run_order_label(**kwargs) -> CommandResponse:
        raise RuntimeError("缺少 JST_COOKIE")

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_label", fake_run_order_label)

    result = runner.invoke(app, ["--json", "jst", "order", "label", "--order-id", "123456"])

    assert result.exit_code == 1
    assert '"success": false' in result.stdout
    assert '"error": "缺少 JST_COOKIE"' in result.stdout


def test_jst_order_remark_json(monkeypatch) -> None:
    def fake_run_order_remark(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order remark",
            data={
                "mode": "dry-run",
                "results": [{"order_no": kwargs["order_ids"][0], "status": "success", "o_id": "123"}],
                "remark_text": kwargs["remark_text"],
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_remark", fake_run_order_remark)

    result = runner.invoke(
        app,
        ["--json", "jst", "order", "remark", "--order-id", "123456", "--remark-text", "测试备注"],
    )

    assert result.exit_code == 0
    assert '"command": "order remark"' in result.stdout
    assert '"status": "success"' in result.stdout
    assert '"测试备注"' in result.stdout


def test_jst_order_query_json(monkeypatch) -> None:
    def fake_run_order_query(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order query",
            data={
                "filters": {
                    "date": kwargs["date_value"],
                    "order_ids": kwargs["order_ids"],
                    "shop_name": kwargs["shop_name"],
                    "status": kwargs["status"],
                    "keyword": kwargs["keyword"],
                    "limit": kwargs["limit"],
                },
                "orders": [
                    {
                        "order_id": "OID1",
                        "shop_name": kwargs["shop_name"],
                        "status": kwargs["status"],
                        "items": [{"product_code": "AMY001", "product_name": "按摩椅"}],
                    }
                ],
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_query", fake_run_order_query)

    result = runner.invoke(
        app,
        [
            "--json",
            "jst",
            "order",
            "query",
            "--date",
            "2026-06-02",
            "--order-id",
            "LP00820708449401",
            "--shop-name",
            "测试店铺",
            "--status",
            "已付款待审核",
            "--keyword",
            "按摩椅",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert '"command": "order query"' in result.stdout
    assert '"order_id": "OID1"' in result.stdout
    assert '"LP00820708449401"' in result.stdout
    assert '"keyword": "按摩椅"' in result.stdout


def test_jst_order_logistics_help() -> None:
    result = runner.invoke(app, ["jst", "order", "logistics", "--help"])

    assert result.exit_code == 0
    assert "--order-id" in result.stdout
    assert "--outer-order-id" in result.stdout
    assert "--input" in result.stdout
    assert "--limit" in result.stdout
    assert "learn" in result.stdout


def test_jst_order_logistics_json(monkeypatch) -> None:
    def fake_run_order_logistics(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order logistics",
            data={
                "outer_order_id": kwargs["outer_order_ids"][0],
                "logistics_no": "SF123456",
                "logistics_company": "顺丰速运",
                "signed": True,
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_logistics", fake_run_order_logistics)

    result = runner.invoke(app, ["--json", "jst", "order", "logistics", "--outer-order-id", "TB123"])

    assert result.exit_code == 0
    assert '"command": "order logistics"' in result.stdout
    assert '"logistics_no": "SF123456"' in result.stdout
    assert '"signed": true' in result.stdout


def test_jst_order_logistics_batch_json(monkeypatch, tmp_path) -> None:
    input_path = tmp_path / "orders.txt"
    input_path.write_text("TB003\nTB004\n", encoding="utf-8")

    def fake_run_order_logistics(**kwargs) -> CommandResponse:
        assert kwargs["order_ids"] == ["SO001"]
        assert kwargs["outer_order_ids"] == ["TB001", "TB002"]
        assert kwargs["input_path"] == str(input_path)
        assert kwargs["limit"] == 3
        return CommandResponse(
            success=False,
            platform="jst",
            command="order logistics",
            data={
                "summary": {"total": 3, "success": 2, "failed": 1},
                "items": [
                    {"outer_order_id": "TB001", "success": True},
                    {"outer_order_id": "TB002", "success": False, "error": "聚水潭未找到指定订单"},
                    {"order_id": "SO001", "success": True},
                ],
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_logistics", fake_run_order_logistics)

    result = runner.invoke(
        app,
        [
            "--json",
            "jst",
            "order",
            "logistics",
            "--outer-order-id",
            "TB001",
            "--outer-order-id",
            "TB002",
            "--order-id",
            "SO001",
            "--input",
            str(input_path),
            "--limit",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert '"total": 3' in result.stdout
    assert '"failed": 1' in result.stdout
    assert '"items"' in result.stdout


def test_jst_order_logistics_learn_json(monkeypatch) -> None:
    def fake_learn_order_logistics(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order logistics learn",
            data={
                "site": "jst_erp",
                "scene": "order_logistics_trace",
                "next_command": "ops --json jst order logistics --outer-order-id <订单号>",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.learn_order_logistics", fake_learn_order_logistics)

    result = runner.invoke(app, ["--json", "jst", "order", "logistics", "learn", "--outer-order-id", "TB123"])

    assert result.exit_code == 0
    assert '"command": "order logistics learn"' in result.stdout
    assert '"order_logistics_trace"' in result.stdout


def test_jst_order_invoice_help() -> None:
    result = runner.invoke(app, ["jst", "order", "invoice", "--help"])

    assert result.exit_code == 0
    assert "--order-id" in result.stdout
    assert "--shop-name" in result.stdout
    assert "--invoice-entity" in result.stdout
    assert "--title" in result.stdout
    assert "--tax-no" in result.stdout
    assert "--amount" in result.stdout
    assert "learn" in result.stdout


def test_jst_order_invoice_json(monkeypatch) -> None:
    def fake_run_order_invoice_workorder(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order invoice",
            data={
                "order_id": kwargs["order_id"],
                "invoice_type": kwargs["invoice_type"],
                "shop_name": kwargs["shop_name"],
                "invoice_entity": kwargs["invoice_entity"],
                "title": kwargs["title"],
                "tax_no": kwargs["tax_no"],
                "amount": kwargs["amount"],
                "quantity": kwargs["quantity"],
                "submitted": kwargs["execute"],
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_invoice_workorder", fake_run_order_invoice_workorder)

    result = runner.invoke(
        app,
        [
            "--json",
            "jst",
            "order",
            "invoice",
            "--order-id",
            "TB123",
            "--shop-name",
            "奥克斯索隆专卖店",
            "--invoice-entity",
            "福安市索隆电子有限公司",
            "--title",
            "YOUR_ACCOUNT",
            "--tax-no",
            "91330000TEST",
            "--address",
            "YOUR_ACCOUNT",
            "--phone",
            "YOUR_ACCOUNT",
            "--bank",
            "YOUR_ACCOUNT",
            "--bank-account",
            "YOUR_ACCOUNT",
            "--amount",
            "128.50",
        ],
    )

    assert result.exit_code == 0
    assert '"command": "order invoice"' in result.stdout
    assert '"invoice_type": "专用发票"' in result.stdout
    assert '"shop_name": "奥克斯索隆专卖店"' in result.stdout
    assert '"invoice_entity": "福安市索隆电子有限公司"' in result.stdout
    assert '"quantity": 1' in result.stdout
    assert '"submitted": false' in result.stdout


def test_jst_order_invoice_learn_json(monkeypatch) -> None:
    def fake_learn_order_invoice_workorder(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order invoice learn",
            data={
                "site": "jst_erp",
                "scene": "order_invoice_workorder",
                "next_command": "ops --json jst order invoice --order-id <订单号> ...",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.learn_order_invoice_workorder", fake_learn_order_invoice_workorder)

    result = runner.invoke(app, ["--json", "jst", "order", "invoice", "learn"])

    assert result.exit_code == 0
    assert '"command": "order invoice learn"' in result.stdout
    assert '"order_invoice_workorder"' in result.stdout


def test_jst_auth_check_json(monkeypatch) -> None:
    def fake_check_auth() -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="auth check",
            data={
                "site": "jst_erp",
                "scene": "order_list",
                "status": "valid",
                "reason": "接口返回 200，session 可用",
                "source": "sessionhub",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.check_auth", fake_check_auth)

    result = runner.invoke(app, ["--json", "jst", "auth", "check"])

    assert result.exit_code == 0
    assert '"status": "valid"' in result.stdout
    assert '"source": "sessionhub"' in result.stdout


def test_jst_auth_ensure_json(monkeypatch) -> None:
    def fake_ensure_auth() -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="auth ensure",
            data={
                "site": "jst_erp",
                "scene": "order_list",
                "status": "valid",
                "source": "sessionhub",
                "action": "ensure",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.ensure_auth", fake_ensure_auth)

    result = runner.invoke(app, ["--json", "jst", "auth", "ensure"])

    assert result.exit_code == 0
    assert '"command": "auth ensure"' in result.stdout
    assert '"action": "ensure"' in result.stdout


def test_jst_auth_capture_json(monkeypatch) -> None:
    def fake_capture_auth() -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="auth capture",
            data={
                "site": "jst_erp",
                "scene": "order_list",
                "status": "valid",
                "source": "sessionhub",
                "action": "capture",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.capture_auth", fake_capture_auth)

    result = runner.invoke(app, ["--json", "jst", "auth", "capture"])

    assert result.exit_code == 0
    assert '"command": "auth capture"' in result.stdout
    assert '"action": "capture"' in result.stdout


def test_tmcs_auth_check_json(monkeypatch) -> None:
    def fake_tmcs_check_auth() -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="auth check",
            data={
                "site": "tmall_chaoshi",
                "scene": "maochao_item_search",
                "status": "valid",
                "reason": "接口返回 200，session 可用",
                "source": "sessionhub",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.tmcs.platform.check_auth", fake_tmcs_check_auth)

    result = runner.invoke(app, ["--json", "tmcs", "auth", "check"])

    assert result.exit_code == 0
    assert '"scene": "maochao_item_search"' in result.stdout
    assert '"source": "sessionhub"' in result.stdout


def test_jst_order_stats_help() -> None:
    result = runner.invoke(app, ["jst", "order", "stats", "--help"])

    assert result.exit_code == 0
    assert "--date" in result.stdout
    assert "learn" in result.stdout


def test_jst_order_stats_json(monkeypatch) -> None:
    def fake_run_order_stats(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order stats",
            data={
                "date": "2026-05-16",
                "store": "（猫超）福安市启明工贸有限公司（肖国清）",
                "order_count": 12,
                "paid_amount": 3456.78,
                "scene": "profit_multi_dimension_report",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_stats", fake_run_order_stats)

    result = runner.invoke(app, ["--json", "jst", "order", "stats"])

    assert result.exit_code == 0
    assert '"command": "order stats"' in result.stdout
    assert '"paid_amount": 3456.78' in result.stdout


def test_jst_order_stats_learn_json(monkeypatch) -> None:
    def fake_learn_order_stats(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="order stats learn",
            data={
                "scene": "profit_multi_dimension_report",
                "source": "sessionhub_9222",
                "next_command": "ops --json jst order stats",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.learn_order_stats", fake_learn_order_stats)

    result = runner.invoke(app, ["--json", "jst", "order", "stats", "learn"])

    assert result.exit_code == 0
    assert '"command": "order stats learn"' in result.stdout
    assert '"profit_multi_dimension_report"' in result.stdout


def test_jst_order_stats_json_error(monkeypatch) -> None:
    def fake_run_order_stats(**kwargs) -> CommandResponse:
        raise RuntimeError("未找到订单统计模板")

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_order_stats", fake_run_order_stats)

    result = runner.invoke(app, ["--json", "jst", "order", "stats"])

    assert result.exit_code == 1
    assert '"success": false' in result.stdout
    assert '"error": "未找到订单统计模板"' in result.stdout


def test_jst_product_help() -> None:
    result = runner.invoke(app, ["jst", "product", "--help"])

    assert result.exit_code == 0
    assert "sync" in result.stdout
    assert "learn" in result.stdout


def test_jst_product_sync_json(monkeypatch) -> None:
    def fake_run_product_sync(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="product sync",
            data={
                "source": "~/Desktop/电商Brain/02-运营店铺/主数据/聚水潭商品资料（最新）.xlsx",
                "used_backend_export": True,
                "downloaded": True,
                "keep_brands": ["奥克斯", "苏泊尔"],
                "output_path": "~/Desktop/电商Brain/02-运营店铺/主数据/聚水潭商品资料（最新）.xlsx",
                "scene": "product_export",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_product_sync", fake_run_product_sync)

    result = runner.invoke(app, ["--json", "jst", "product", "sync"])

    assert result.exit_code == 0
    assert '"command": "product sync"' in result.stdout
    assert '"downloaded": true' in result.stdout


def test_jst_product_sync_dry_run_json(monkeypatch) -> None:
    def fake_run_product_sync(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="product sync",
            data={
                "source": "~/Desktop/电商Brain/02-运营店铺/主数据/聚水潭商品资料（最新）.xlsx",
                "used_backend_export": True,
                "downloaded": False,
                "keep_brands": ["奥克斯", "苏泊尔"],
                "dry_run": True,
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_product_sync", fake_run_product_sync)

    result = runner.invoke(app, ["--json", "jst", "product", "sync", "--dry-run"])

    assert result.exit_code == 0
    assert '"dry_run": true' in result.stdout


def test_jst_product_sync_keep_brands_multi_args(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_product_sync(**kwargs) -> CommandResponse:
        captured.update(kwargs)
        return CommandResponse(
            success=True,
            platform="jst",
            command="product sync",
            data={"keep_brands": kwargs["keep_brands"]},
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_product_sync", fake_run_product_sync)

    result = runner.invoke(app, ["--json", "jst", "product", "sync", "--keep-brands", "奥克斯", "苏泊尔"])

    assert result.exit_code == 0
    assert captured["keep_brands"] == ["奥克斯", "苏泊尔"]


def test_jst_product_learn_json(monkeypatch) -> None:
    def fake_learn_product_sync(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="product learn",
            data={
                "scene": "product_export",
                "source": "sessionhub",
                "next_command": "ops --json jst product sync",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.learn_jst_product_sync", fake_learn_product_sync)

    result = runner.invoke(app, ["--json", "jst", "product", "learn"])

    assert result.exit_code == 0
    assert '"command": "product learn"' in result.stdout
    assert '"product_export"' in result.stdout


def test_jst_product_sync_json_error(monkeypatch) -> None:
    def fake_run_product_sync(**kwargs) -> CommandResponse:
        raise RuntimeError("未找到商品同步模板")

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_product_sync", fake_run_product_sync)

    result = runner.invoke(app, ["--json", "jst", "product", "sync"])

    assert result.exit_code == 1
    assert '"success": false' in result.stdout
    assert '"error": "未找到商品同步模板"' in result.stdout


def test_jst_profit_help() -> None:
    result = runner.invoke(app, ["jst", "profit", "--help"])

    assert result.exit_code == 0
    assert "yesterday" in result.stdout
    assert "month" in result.stdout
    assert "learn" in result.stdout


def test_jst_profit_yesterday_real_json(monkeypatch) -> None:
    def fake_run_yesterday_profit(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="profit yesterday",
            data={
                "date": "2026-05-15",
                "store": "（猫超）福安市启明工贸有限公司（肖国清）",
                "profit": 929.8,
                "metric_field": "经营利润",
                "scene": "business_profit_multi_dimension_report",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_yesterday_profit", fake_run_yesterday_profit)

    result = runner.invoke(app, ["--json", "jst", "profit", "yesterday"])

    assert result.exit_code == 0
    assert '"command": "profit yesterday"' in result.stdout
    assert '"profit": 929.8' in result.stdout
    assert '"经营利润"' in result.stdout


def test_jst_profit_learn_json(monkeypatch) -> None:
    def fake_learn_profit(**kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="profit learn",
            data={
                "scene": "business_profit_multi_dimension_report",
                "source": "sessionhub_9222",
                "next_command": "ops --json jst profit yesterday",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.learn_jst_profit_scene", fake_learn_profit)

    result = runner.invoke(app, ["--json", "jst", "profit", "learn"])

    assert result.exit_code == 0
    assert '"command": "profit learn"' in result.stdout
    assert '"business_profit_multi_dimension_report"' in result.stdout


def test_jst_profit_month_real_json(monkeypatch) -> None:
    def fake_get_month_profit(*, month: str, **kwargs) -> CommandResponse:
        return CommandResponse(
            success=True,
            platform="jst",
            command="profit month",
            data={
                "month": month,
                "store": "（猫超）福安市启明工贸有限公司（肖国清）",
                "profit": 45678.9,
                "metric_field": "经营利润",
                "scene": "business_profit_multi_dimension_report",
            },
        )

    monkeypatch.setattr("ops_cli.platforms.jst.platform.get_month_profit", fake_get_month_profit)

    result = runner.invoke(app, ["--json", "jst", "profit", "month", "--month", "2026-04"])

    assert result.exit_code == 0
    assert '"command": "profit month"' in result.stdout
    assert '"month": "2026-04"' in result.stdout
    assert '"profit": 45678.9' in result.stdout


def test_jst_profit_yesterday_json_error(monkeypatch) -> None:
    def fake_run_yesterday_profit(**kwargs) -> CommandResponse:
        raise RuntimeError("未找到利润统计模板")

    monkeypatch.setattr("ops_cli.platforms.jst.platform.run_yesterday_profit", fake_run_yesterday_profit)

    result = runner.invoke(app, ["--json", "jst", "profit", "yesterday"])

    assert result.exit_code == 1
    assert '"success": false' in result.stdout
    assert '"error": "未找到利润统计模板"' in result.stdout
