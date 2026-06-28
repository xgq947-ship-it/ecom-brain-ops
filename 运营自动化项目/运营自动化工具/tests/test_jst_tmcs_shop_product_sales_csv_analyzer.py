from __future__ import annotations

import csv
from pathlib import Path

import pytest

from workflows.jst_tmcs_shop_product_sales_analysis import csv_analyzer


NUM_COLS = 82


def _row(*, sku: str, name: str, qty: float, amt: float, cost: float, gross: float,
         expense: float, ad_fee: float, profit: float, refund_qty: float, ad_consume: float) -> list[str]:
    row = [""] * NUM_COLS
    row[0] = sku
    row[3] = name
    row[6] = str(qty)
    row[7] = str(amt)
    row[8] = str(cost)
    row[19] = str(gross)
    row[21] = str(expense)
    row[23] = str(ad_fee)
    row[25] = str(profit)
    row[43] = str(refund_qty)
    row[81] = str(ad_consume)
    return row


def _header(first: str = "店铺款式编码") -> list[str]:
    # 表头用 csv_analyzer 归一化 shim 能识别的列名（按表头名定位列）
    header = [""] * NUM_COLS
    header[0] = first
    header[3] = "款式编码(参考)"
    header[6] = "商品销售数据-商品销售数量(扣退)"
    header[7] = "商品销售数据-商品销售金额(扣退)"
    header[8] = "商品销售数据-商品销售成本(扣退)"
    header[19] = "利润-毛利额"
    header[21] = "利润-费用"
    header[23] = "利润-其中：推广费"
    header[25] = "利润-经营利润"
    header[43] = "退款合计-退款数量合计"
    header[81] = "商品费用-线上推广消耗"
    return header


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def _sample_rows() -> list[list[str]]:
    return [
        # 优先推广：无推广、利润率33%、销量20、均价300、退款率低
        _row(sku="AUX001", name="奥克斯电饭煲", qty=20, amt=6000, cost=4000,
             gross=2000, expense=200, ad_fee=0, profit=2000, refund_qty=1, ad_consume=0),
        # 次级推广：低推广占比、利润率21%、销量8、均价250
        _row(sku="SUP002", name="苏泊尔炒锅", qty=8, amt=2000, cost=1400,
             gross=600, expense=100, ad_fee=10, profit=420, refund_qty=1, ad_consume=40),
        # 推广过高预警：有推广、利润率10%
        _row(sku="SUP003", name="苏泊尔水壶", qty=5, amt=1000, cost=700,
             gross=300, expense=50, ad_fee=50, profit=100, refund_qty=0, ad_consume=50),
        # 重复优先推广编码（验证去重保序）
        _row(sku="AUX001", name="奥克斯电饭煲", qty=15, amt=4500, cost=3000,
             gross=1500, expense=150, ad_fee=0, profit=1600, refund_qty=0, ad_consume=0),
    ]


@pytest.fixture()
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "商品销售情况.csv"
    _write_csv(path, _header(), _sample_rows())
    return path


def test_analyze_outputs_style_codes(sample_csv: Path) -> None:
    result = csv_analyzer.analyze_sales_csv(str(sample_csv))
    assert "AUX001" in result["style_codes"]
    assert "SUP002" in result["style_codes"]
    # 预警商品不进入推广编码清单
    assert "SUP003" not in result["style_codes"]
    assert result["total_rows"] == 4
    # 两条 AUX001(优先) + 一条 SUP002(次级) = 3 行命中；去重后 2 个编码
    assert result["matched_rows"] == 3
    assert result["unique_style_code_count"] == 2
    assert "report_text" in result and "猫超店铺推广分析报告" in result["report_text"]


def test_style_codes_deduped_and_ordered(sample_csv: Path) -> None:
    result = csv_analyzer.analyze_sales_csv(str(sample_csv))
    codes = result["style_codes"]
    # 去重
    assert len(codes) == len(set(codes))
    assert result["unique_style_code_count"] == len(codes)
    # 保序：优先推广（AUX001）排在次级推广（SUP002）之前
    assert codes.index("AUX001") < codes.index("SUP002")


def test_missing_style_code_field_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    _write_csv(path, _header(first="款式编码"), _sample_rows())
    with pytest.raises(ValueError, match="店铺款式编码"):
        csv_analyzer.analyze_sales_csv(str(path))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        csv_analyzer.analyze_sales_csv(str(tmp_path / "nope.csv"))
