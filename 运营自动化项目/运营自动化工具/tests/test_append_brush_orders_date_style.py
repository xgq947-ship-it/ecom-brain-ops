from __future__ import annotations

import re

from workflows.append_brush_orders import appender as append_brush_orders


# 模拟一份最小 styles.xml：4 个 cellXf，C 列日期用的基准样式带边框/字体/对齐但 numFmtId=0（常规）。
BASE_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="41" formatCode="#,##0"/></numFmts>'
    '<cellXfs count="4">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="2" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '</cellXfs>'
    '</styleSheet>'
)


def _numfmt_of(styles_xml: str, style_id: int) -> str:
    body = re.search(r'<cellXfs count="\d+">(.*?)</cellXfs>', styles_xml, re.S).group(1)
    xfs = re.findall(r"<xf\b[^>]*?/>|<xf\b[^>]*?>.*?</xf>", body, re.S)
    fmt_id = re.search(r'numFmtId="(\d+)"', xfs[style_id]).group(1)
    codes = dict(re.findall(r'<numFmt numFmtId="(\d+)" formatCode="(.*?)"\s*/>', styles_xml))
    return codes.get(fmt_id, "")


def test_ensure_date_style_appends_date_format_cloning_base() -> None:
    new_xml, new_id = append_brush_orders.ensure_date_style(BASE_STYLES, base_style_id=2)

    # 追加到末尾，不动已有样式
    assert new_id == 4
    body = re.search(r'<cellXfs count="(\d+)">(.*?)</cellXfs>', new_xml, re.S)
    assert body.group(1) == "5"
    xfs = re.findall(r"<xf\b[^>]*?/>|<xf\b[^>]*?>.*?</xf>", body.group(2), re.S)
    assert len(xfs) == 5

    # 新样式用日期 numFmt，且克隆了基准样式 2 的边框/对齐
    assert _numfmt_of(new_xml, 4) == append_brush_orders.DATE_FORMAT_CODE
    assert 'borderId="2"' in xfs[4]
    assert "<alignment vertical=\"center\"/>" in xfs[4]
    assert 'applyNumberFormat="1"' in xfs[4]

    # 自定义 numFmtId 需 >= 164，避免覆盖内置/已有
    fmt_id = int(re.search(r'numFmtId="(\d+)"', xfs[4]).group(1))
    assert fmt_id >= 164


def test_ensure_date_style_is_idempotent() -> None:
    once_xml, once_id = append_brush_orders.ensure_date_style(BASE_STYLES, base_style_id=2)
    twice_xml, twice_id = append_brush_orders.ensure_date_style(once_xml, base_style_id=2)

    # 第二次复用已存在的日期样式，不再重复追加
    assert twice_id == once_id
    assert twice_xml.count(append_brush_orders.DATE_FORMAT_CODE) == 1
    count = re.search(r'<cellXfs count="(\d+)">', twice_xml).group(1)
    assert count == "5"
