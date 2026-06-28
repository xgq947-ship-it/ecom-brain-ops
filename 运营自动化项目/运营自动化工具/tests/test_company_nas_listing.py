from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import workflows.company_nas_listing.listing as cnl
from workflows.company_nas_listing.listing import (
    build_index_lister,
    copy_relative_path,
    index_freshness,
    pick_library_title,
    select_files_resolved,
    selected_files,
)


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")


def _sample_tree(tmp_path: Path) -> Path:
    product = tmp_path / "703"
    touch(product / "主图" / "主图" / "a_800.jpg")
    touch(product / "sku" / "sku_800.jpeg")
    touch(product / "详情切片" / "790" / "detail_a.jpg")
    touch(product / "详情切片" / "x_790.gif")
    touch(product / "详情切片" / "750" / "detail_b.jpg")
    touch(product / "场景图" / "scene_800.jpg")
    touch(product / "白底透明" / "white_800.png")
    return product


def _index_payload(base: Path, *, updated_at: str | None = None, files: bool = True) -> dict:
    records = []
    for root, dirs, names in os.walk(base):
        for d in dirs:
            records.append({"type": "dir", "path": str(Path(root) / d)})
        if files:
            for name in names:
                records.append({"type": "file", "path": str(Path(root) / name)})
    return {"updated_at": updated_at or datetime.now().isoformat(timespec="seconds"), "records": records}


def test_selected_files_keeps_detail_790_layouts(tmp_path: Path) -> None:
    product = _sample_tree(tmp_path)

    rel_paths = {p.relative_to(product) for p in selected_files(product, include_buyer_show=False)}

    assert Path("主图/主图/a_800.jpg") in rel_paths
    assert Path("sku/sku_800.jpeg") in rel_paths
    assert Path("详情切片/790/detail_a.jpg") in rel_paths
    assert Path("详情切片/x_790.gif") in rel_paths
    assert Path("详情切片/750/detail_b.jpg") not in rel_paths
    assert Path("场景图/scene_800.jpg") in rel_paths
    assert Path("白底透明/white_800.png") in rel_paths


def test_index_lister_selection_matches_live(tmp_path: Path) -> None:
    # 方案②：索引内存选材与实时遍历选材必须给出完全一致的结果（规则单一真源）。
    product = _sample_tree(tmp_path)
    live = selected_files(product, include_buyer_show=False)
    lister = build_index_lister(_index_payload(product))
    assert lister is not None
    indexed = selected_files(product, include_buyer_show=False, lister=lister)
    assert sorted(str(p) for p in indexed) == sorted(str(p) for p in live)


def test_select_files_resolved_uses_index_then_live(tmp_path: Path) -> None:
    product = _sample_tree(tmp_path)
    files, source = select_files_resolved(product, False, _index_payload(product))
    assert source == "index"
    assert files


def test_select_files_resolved_falls_back_to_live_without_file_records(tmp_path: Path) -> None:
    # 索引只有目录、无文件层级（未建全量索引）→ 回退实时遍历。
    product = _sample_tree(tmp_path)
    files, source = select_files_resolved(product, False, _index_payload(product, files=False))
    assert source == "live"
    assert files


def test_select_files_resolved_falls_back_when_model_missing_in_index(tmp_path: Path) -> None:
    # 新增产品不在索引中 → 索引选材为空 → 回退实时遍历命中真实文件。
    product = _sample_tree(tmp_path)
    other = tmp_path / "999"
    touch(other / "主图" / "主图" / "z_800.jpg")
    files, source = select_files_resolved(product, False, _index_payload(other))
    assert source == "live"
    assert files


def test_build_index_lister_none_without_files(tmp_path: Path) -> None:
    product = _sample_tree(tmp_path)
    assert build_index_lister(_index_payload(product, files=False)) is None
    assert build_index_lister(None) is None


def test_copy_relative_path_splits_main_and_secondary(tmp_path: Path) -> None:
    # 纯「主图」→ 主图/；副主图/功能性主图/功能主图 → 副图/。
    src = tmp_path / "841"
    cases = {
        "主图/主图/a_800.jpg": Path("主图/a_800.jpg"),
        "主图/副主图/b_800.jpg": Path("副图/b_800.jpg"),
        "主图/功能性主图/c_800.jpg": Path("副图/c_800.jpg"),
        "主图/功能主图/d_800.jpg": Path("副图/d_800.jpg"),
        "主附图/主图/e_800.jpg": Path("主图/e_800.jpg"),
        "主附图/副主图/f_800.jpg": Path("副图/f_800.jpg"),
    }
    for rel, expected in cases.items():
        assert copy_relative_path(src, src / rel) == expected, rel


def test_index_freshness_flags_stale() -> None:
    fresh = index_freshness({"updated_at": datetime.now().isoformat(timespec="seconds")})
    assert fresh["stale"] is False and fresh["age_days"] == 0
    old = index_freshness({"updated_at": (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")})
    assert old["stale"] is True and old["age_days"] == 30
    assert index_freshness(None)["stale"] is True
    assert index_freshness({})["stale"] is True


def _make_title_library(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "足疗机"
    ws.append(["类目", "品牌", "商品标题"])
    ws.append(["足疗机", "奥克斯", "奥克斯足疗机A"])
    ws.append(["足疗机", "奥克斯", "奥克斯足疗机B"])
    ws.append(["足疗机", "", "通用足疗机C"])
    ws.append(["足疗机", "美的", "美的足疗机D"])
    wb.save(path)


def test_pick_library_title_filters_by_category_and_brand(tmp_path: Path, monkeypatch) -> None:
    lib = tmp_path / "标题库.xlsx"
    _make_title_library(lib)
    monkeypatch.setattr(cnl, "_TITLE_LIBRARY_CACHE", None)
    monkeypatch.setattr(cnl, "get_path", lambda name: lib if name == "massage_title_library_file" else Path("/nonexistent"))

    # 类目+品牌命中：只在该品牌标题中随机
    for _ in range(20):
        assert pick_library_title("足疗机", "奥克斯") in {"奥克斯足疗机A", "奥克斯足疗机B"}
    # 品牌无命中：回退到类目下任意标题
    assert pick_library_title("足疗机", "不存在品牌") is not None
    # 类目缺失：返回 None，调用方回退规则生成
    assert pick_library_title("不存在类目", "奥克斯") is None


def test_pick_library_title_missing_library_returns_none(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cnl, "_TITLE_LIBRARY_CACHE", None)
    monkeypatch.setattr(cnl, "get_path", lambda name: tmp_path / "缺失.xlsx")
    assert pick_library_title("足疗机", "奥克斯") is None
