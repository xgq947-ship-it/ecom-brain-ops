from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from workflows.jst_massage_chair_order_remark.excel_lookup import load_massage_chair_mapping


def test_excel_lookup_loads_product_code_mapping(tmp_path: Path) -> None:
    path = tmp_path / "按摩椅资料表.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["商品编码", "商品名称"])
    worksheet.append(["AMYAUX03A81201", "景拓奥克斯AUX-03A-812静谧蓝"])
    workbook.save(path)

    mapping = load_massage_chair_mapping(path)

    assert mapping["AMYAUX03A81201"] == "景拓奥克斯AUX-03A-812静谧蓝"
