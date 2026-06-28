from __future__ import annotations

from pathlib import Path

import pytest

from ops_cli.capabilities import capability_ids, get_capability
from ops_cli.cli import app  # noqa: F401
from ops_cli.platforms.tmcs import fund


def test_fund_capabilities_registered() -> None:
    registered = capability_ids()
    assert "tmcs.fund.receivable-bill.sum" in registered
    assert "tmcs.fund.promotion-balance.sum" in registered

    spec = get_capability("tmcs.fund.receivable-bill.sum")
    assert spec.platform == "tmcs"
    assert spec.command == "fund receivable-bill sum"
    assert "fund_receivable_bill_sum" in spec.scenes

    spec = get_capability("tmcs.fund.promotion-balance.sum")
    assert spec.platform == "tmcs"
    assert spec.command == "fund promotion-balance sum"
    assert "fund_promotion_balance_sum" in spec.scenes


def test_extract_receivable_amounts_from_page_text() -> None:
    text = """
    对账单/查询
    商家开票含税总额
    ¥123.45
    1,234.56
    -78.90
    其他字段 999.99
    """
    assert fund.extract_receivable_amounts_from_text(text) == [123.45, 1234.56, -78.9]


def test_extract_receivable_amounts_from_table_rows_uses_exact_column() -> None:
    headers = [
        "结算含税总额",
        "货款含税总额",
        "票扣含税总额",
        "商家开票含税总额",
        "商家开票税额",
    ]
    rows = [
        ["84,914.63", "88,535.66", "-3,444.85", "85,090.81", "9,789.20"],
        ["63,206.46", "69,603.97", "-6,340.35", "63,263.62", "7,278.09"],
        ["76,470.33", "79,168.30", "-2,618.23", "76,550.07", "8,806.64"],
    ]

    assert fund.extract_receivable_amounts_from_table_rows(headers, rows) == [85090.81, 63263.62, 76550.07]


def test_extract_receivable_amounts_from_table_rows_filters_month() -> None:
    headers = ["账单周期", "商家开票含税总额"]
    rows = [
        ["2026-06-01~2026-06-10", "85,090.81"],
        ["2026-05-21~2026-05-31", "63,263.62"],
        ["2026-05-11~2026-05-20", "76,550.07"],
        ["2026-05-01~2026-05-10", "57,301.57"],
        ["2026-04-21~2026-04-30", "26,117.13"],
    ]

    assert fund.extract_receivable_amounts_from_table_rows(headers, rows, month="2026-05") == [
        63263.62,
        76550.07,
        57301.57,
    ]


def test_receivable_month_range_uses_next_month_end() -> None:
    assert fund.receivable_bill_month_range("2026-05") == ("2026-05-01", "2026-06-01")
    assert fund.receivable_bill_month_range("2026-12") == ("2026-12-01", "2027-01-01")


def test_extract_receivable_amounts_missing_field_raises() -> None:
    with pytest.raises(RuntimeError, match="FIELD_NOT_FOUND"):
        fund.extract_receivable_amounts_from_text("对账单/查询 金额 100")


def test_extract_promotion_balances_from_page_text() -> None:
    text = """
    推广平台
    聚宝盆余额 ¥100.00
    智多星余额 200.50 元
    万相台余额 1,300.25
    """
    balances = fund.extract_promotion_balances_from_text(text)
    assert balances == {"jubao_pen": 100.0, "zhiduoxing": 200.5, "wanxiangtai": 1300.25}
    assert fund.sum_promotion_balances(balances) == 1600.75


def test_extract_promotion_balances_from_overview_page_text() -> None:
    text = """
    推广平台
    福安市启明工贸有限公司
    聚宝盆余额 ?
    ¥1500
    转账
    立即充值
    智多星余额 ?
    ¥269.23
    万相台余额 ?
    ¥785.73
    """

    assert fund.extract_promotion_balances_from_text(text) == {
        "jubao_pen": 1500.0,
        "zhiduoxing": 269.23,
        "wanxiangtai": 785.73,
    }


def test_promotion_platform_url_uses_overview_page() -> None:
    assert "vendor_jbp_page_new" in fund.TMCS_PROMOTION_PLATFORM_URL


def test_extract_promotion_balances_missing_field_raises() -> None:
    with pytest.raises(RuntimeError, match="FIELD_NOT_FOUND"):
        fund.extract_promotion_balances_from_text("推广平台 聚宝盆余额 100 智多星余额 200")


def test_receivable_dry_run_returns_simulated_and_screenshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = fund.run_receivable_bill_sum(month="2026-05", screenshot_dir=tmp_path / "shots", dry_run=True)

    assert response.success is True
    assert response.command == "fund receivable-bill sum"
    data = response.data
    assert data["month"] == "2026-05"
    assert data["field_name"] == "商家开票含税总额"
    assert data["total_amount"] == 802.35
    assert data["source"] == "simulated"
    assert data["simulated"] is True
    assert Path(data["screenshot_path"]).is_file()


def test_promotion_dry_run_returns_simulated_and_screenshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = fund.run_promotion_balance_sum(screenshot_dir=tmp_path / "shots", dry_run=True)

    assert response.success is True
    assert response.command == "fund promotion-balance sum"
    data = response.data
    assert data["balances"] == {"jubao_pen": 100.0, "zhiduoxing": 200.0, "wanxiangtai": 300.0}
    assert data["total_amount"] == 600.0
    assert data["source"] == "simulated"
    assert data["simulated"] is True
    assert Path(data["screenshot_path"]).is_file()


def test_receivable_real_read_uses_page_text_and_screenshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    screenshot = tmp_path / "receivable.png"
    screenshot.write_bytes(b"png")
    monkeypatch.setattr(
        fund,
        "_read_receivable_bill_page",
        lambda *, month, screenshot_dir: ([123.45, 678.90], screenshot),
    )

    response = fund.run_receivable_bill_sum(month="2026-05", screenshot_dir=tmp_path, dry_run=False)

    assert response.data["amounts"] == [123.45, 678.9]
    assert response.data["total_amount"] == 802.35
    assert response.data["source"] == "page"
    assert response.data["simulated"] is False
    assert response.data["screenshot_path"] == str(screenshot)


def test_promotion_real_read_uses_page_text_and_screenshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    screenshot = tmp_path / "promotion.png"
    screenshot.write_bytes(b"png")
    monkeypatch.setattr(
        fund,
        "_read_promotion_balance_page",
        lambda *, screenshot_dir: ("聚宝盆余额 100 智多星余额 200 万相台余额 300", screenshot),
    )

    response = fund.run_promotion_balance_sum(screenshot_dir=tmp_path, dry_run=False)

    assert response.data["balances"] == {"jubao_pen": 100.0, "zhiduoxing": 200.0, "wanxiangtai": 300.0}
    assert response.data["total_amount"] == 600.0
    assert response.data["source"] == "page"
    assert response.data["simulated"] is False
    assert response.data["screenshot_path"] == str(screenshot)
