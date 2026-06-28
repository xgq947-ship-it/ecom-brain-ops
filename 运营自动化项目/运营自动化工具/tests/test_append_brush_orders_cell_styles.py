from __future__ import annotations

import re
import tempfile
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import load_workbook

from workflows.append_brush_orders import appender as append_brush_orders
from workflows.append_brush_orders.appender import SourceRecord


# 一份最小但合法的登记表：styles.xml 只有 10 个 cellXf（合法样式 id 0–9），
# 存量数据行（第 3 行）D 列无 s（隐式 0）、H 列 s=7，C 列日期用 s=6。
# 旧代码硬编码的 s=17 / s=12 在这里都越界，可复现 IndexError。
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
    '</Types>'
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    '</Relationships>'
)

_WB_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '</Relationships>'
)

_WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="天猫超市6月刷单登记明细" sheetId="1" r:id="rId1"/></sheets>'
    '</workbook>'
)

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="1"><font><sz val="11"/><name val="宋体"/></font></fonts>'
    '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
    '<borders count="3">'
    '<border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border>'
    '<border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/></border>'
    '</borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="10">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'  # 0
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0"/>'  # 1
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="2" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'  # 2
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'  # 3
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'  # 4
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'  # 5
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="2" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'  # 6 (date base)
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="2" xfId="0" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>'  # 7 (H)
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'  # 8
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="2" xfId="0"/>'  # 9
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="常规" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)

_SHEET = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<dimension ref="A1:K3"/>'
    '<sheetData>'
    '<row r="1"><c r="B1" s="4" t="inlineStr"><is><t>天猫超市6月刷单登记明细</t></is></c></row>'
    '<row r="2">'
    '<c r="A2" s="2" t="inlineStr"><is><t>序号</t></is></c>'
    '</row>'
    '<row r="3" customHeight="1" spans="1:11">'
    '<c r="A3" s="2"><v>1</v></c>'
    '<c r="B3" s="2" t="inlineStr"><is><t>刷手甲</t></is></c>'
    '<c r="C3" s="6"><v>46174</v></c>'
    '<c r="D3" t="inlineStr"><is><t>OLD-ORDER</t></is></c>'
    '<c r="E3" s="2"><v>100</v></c>'
    '<c r="F3" s="2"><v>5</v></c>'
    '<c r="G3" s="2"/>'
    '<c r="H3" s="7"/>'
    '<c r="I3" s="2" t="inlineStr"><is><t>P001</t></is></c>'
    '<c r="J3" s="2" t="inlineStr"><is><t>商品甲</t></is></c>'
    '<c r="K3" t="inlineStr"><is><t>是</t></is></c>'
    '</row>'
    '</sheetData>'
    '</worksheet>'
)


def _make_register(path: Path) -> None:
    parts = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "xl/workbook.xml": _WORKBOOK,
        "xl/_rels/workbook.xml.rels": _WB_RELS,
        "xl/styles.xml": _STYLES,
        "xl/worksheets/sheet1.xml": _SHEET,
    }
    with ZipFile(path, "w", ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            zf.writestr(name, text)


def _record() -> SourceRecord:
    return SourceRecord(
        order_no="T-NEW-1",
        amount=Decimal("12.3"),
        commission=Decimal("1.5"),
        brusher="刷手乙",
        product_code="P002",
        product_name="商品乙",
        source_file="f",
        source_mtime=0.0,
    )


def _raw_style(sheet_xml: str, cell_ref: str) -> str:
    match = re.search(rf'<c r="{cell_ref}"([^>]*?)/?>', sheet_xml)
    assert match, f"找不到单元格 {cell_ref}"
    return append_brush_orders._cell_style_attr(match.group(1))


def test_cell_style_attr_reads_s_or_defaults_zero() -> None:
    assert append_brush_orders._cell_style_attr(' s="7" ') == "7"
    assert append_brush_orders._cell_style_attr(' r="D3" t="inlineStr"') == "0"


def test_detect_existing_data_row_styles_reads_raw_s() -> None:
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "register.xlsx"
        _make_register(target)
        # 存量数据行：D 无 s（隐式 0），H s=7。
        assert append_brush_orders.detect_existing_data_row_styles(target) == ("0", "7")


def test_patch_workbook_writes_in_bounds_styles_loadable() -> None:
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "register.xlsx"
        _make_register(target)

        append_brush_orders.patch_workbook(
            target=target,
            month=6,
            records_by_day=[(2, [_record()])],
            append_start_row=4,
            first_seq=2,
        )

        # 旧代码写 s=17/12 越界，openpyxl 会 IndexError；修复后必须能正常加载。
        wb = load_workbook(target)
        try:
            assert wb.active["D4"].value == "T-NEW-1"
        finally:
            wb.close()

        with ZipFile(target, "r") as zf:
            sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
            cellxfs_count = int(
                re.search(r'<cellXfs count="(\d+)"', zf.read("xl/styles.xml").decode("utf-8")).group(1)
            )

        # 新行 D/H 样式与存量数据行一致，且落在 styles.xml 合法 cellXf 区间内。
        assert _raw_style(sheet_xml, "D4") == _raw_style(sheet_xml, "D3") == "0"
        assert _raw_style(sheet_xml, "H4") == _raw_style(sheet_xml, "H3") == "7"
        for cell_ref in ("D4", "H4"):
            assert int(_raw_style(sheet_xml, cell_ref)) < cellxfs_count
