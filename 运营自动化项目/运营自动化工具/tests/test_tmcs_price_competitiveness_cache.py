from __future__ import annotations

from pathlib import Path

import pytest

from workflows.tmcs_price_competitiveness_lookup import cache


ROWS = [
    {"item_id": "100", "sku_id": "s1", "title": "甲"},
    {"item_id": "100", "sku_id": "s2", "title": "甲-2"},
    {"item_id": "200", "sku_id": "s3", "title": "乙"},
]


def test_parse_codes_merges_and_dedupes(tmp_path: Path) -> None:
    f = tmp_path / "codes.txt"
    f.write_text("300\n# 注释\n\n200\n", encoding="utf-8")
    codes = cache.parse_codes(
        product_code="100",
        product_codes="100, 400，500",  # 逗号/中文逗号/空格混合
        codes_file=str(f),
    )
    # 去重保序：100(单) 400 500(批量) 300 200(文件)
    assert codes == ["100", "400", "500", "300", "200"]


def test_parse_codes_empty() -> None:
    assert cache.parse_codes(product_code=None, product_codes=None, codes_file=None) == []
    assert cache.parse_codes(product_code="  ", product_codes=",，", codes_file=None) == []


def test_parse_codes_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cache.parse_codes(product_code=None, product_codes=None, codes_file=str(tmp_path / "nope.txt"))


def test_match_code_exact_match_with_skus() -> None:
    res = cache.match_code("100", ROWS)
    assert res["exists"] is True
    assert len(res["matched_items"]) == 2  # 两个 SKU
    assert {m["sku_id"] for m in res["matched_items"]} == {"s1", "s2"}


def test_match_code_not_found() -> None:
    res = cache.match_code("999", ROWS)
    assert res["exists"] is False
    assert res["matched_items"] == []


def test_match_codes_batch() -> None:
    results = cache.match_codes(["100", "999", "200"], ROWS)
    assert [r["exists"] for r in results] == [True, False, True]


def test_cache_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path / "c")
    assert cache.load_cache("2026-06-22") is None
    path = cache.save_cache(
        "2026-06-22",
        {"captured_at": "2026-06-22T10:00:00", "total_rows": 3, "rows": ROWS},
    )
    assert path.exists()
    loaded = cache.load_cache("2026-06-22")
    assert loaded is not None
    assert loaded["list_date"] == "2026-06-22"
    assert loaded["total_rows"] == 3
    assert len(loaded["rows"]) == 3


def test_load_cache_corrupt_returns_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cache, "cache_dir", lambda: tmp_path / "c")
    p = cache.cache_path("2026-06-22")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert cache.load_cache("2026-06-22") is None
