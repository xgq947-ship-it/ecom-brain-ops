"""excel_loader 字段识别 / 数值清洗 / 读取 测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.tmcs_realtime_inventory_watch import excel_loader as xl


def _make_xlsx(path: Path, headers: list[str], rows: list[list]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_read_rows_skips_empty(tmp_path):
    path = tmp_path / "t.xlsx"
    _make_xlsx(path, ["A", "B"], [[1, 2], [None, None], [3, 4]])
    headers, records = xl.read_rows(path)
    assert headers == ["A", "B"]
    assert len(records) == 2
    assert records[0] == {"A": 1, "B": 2}


def test_resolve_field_uses_candidate_order():
    headers = ["商品编码", "实际库存数", "品牌"]
    assert xl.resolve_field(headers, xl.JST_ACTUAL_STOCK, label="实际库存") == "实际库存数"


def test_resolve_field_raises_clear_error():
    headers = ["foo", "bar"]
    with pytest.raises(xl.FieldNotFoundError) as exc:
        xl.resolve_field(headers, xl.JST_PRODUCT_CODE, label="商品编码")
    assert "商品编码" in str(exc.value)
    assert "foo" in str(exc.value)


@pytest.mark.parametrize(
    "raw,expected,empty",
    [
        (None, 0.0, True),
        ("", 0.0, True),
        (12, 12.0, False),
        ("1,234", 1234.0, False),
        (" 5 件", 5.0, False),
        ("-3", -3.0, False),
        ("abc", 0.0, True),
    ],
)
def test_clean_number(raw, expected, empty):
    value, is_empty = xl.clean_number(raw)
    assert value == expected
    assert is_empty == empty
