from __future__ import annotations

import sys

from workflows.append_brush_orders import appender as append_brush_orders
from tasks import append_brush_orders as task_entry


def test_real_append_preflights_jst_before_input_copy_or_workbook_writes(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        append_brush_orders,
        "preflight_platform_auth",
        lambda platform: calls.append(f"preflight:{platform}"),
        raising=False,
    )
    monkeypatch.setattr(append_brush_orders, "has_xlsx_files", lambda path: False)
    monkeypatch.setattr(
        append_brush_orders,
        "copy_wechat_source_files",
        lambda month, day, print_skipped=False: calls.append("copy") or [],
    )
    monkeypatch.setattr(append_brush_orders, "read_all_source_batches", lambda explicit_files=None: [])
    monkeypatch.setattr(append_brush_orders, "write_latest_brush_orders", lambda orders: calls.append("latest"))
    monkeypatch.setattr(append_brush_orders, "clear_source_dir", lambda: calls.append("clear"))

    append_brush_orders.run(dry_run=False)

    assert calls[0] == "preflight:jst"
    assert calls == ["preflight:jst", "copy", "latest", "clear"]


def test_dry_run_append_does_not_preflight_jst(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        append_brush_orders,
        "preflight_platform_auth",
        lambda platform: calls.append(f"preflight:{platform}"),
        raising=False,
    )
    monkeypatch.setattr(append_brush_orders, "has_xlsx_files", lambda path: True)
    monkeypatch.setattr(append_brush_orders, "read_all_source_batches", lambda explicit_files=None: [])

    append_brush_orders.run(dry_run=True)

    assert calls == []


def test_dry_run_reads_wechat_sources_without_copy(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    source_file = tmp_path / "奥克斯索隆6🈷️1(1).xlsx"
    source_file.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(append_brush_orders, "SOURCE_DIR", tmp_path / "staged", raising=False)
    monkeypatch.setattr(append_brush_orders, "WECHAT_TARGET_DIR", tmp_path / "target", raising=False)
    monkeypatch.setattr(append_brush_orders, "has_xlsx_files", lambda path: False)
    monkeypatch.setattr(
        append_brush_orders,
        "find_wechat_source_files",
        lambda month, day, print_skipped=False: calls.append(f"find:{month}-{day}") or [source_file],
    )
    monkeypatch.setattr(
        append_brush_orders,
        "copy_wechat_source_files",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run 不应复制微信文件")),
    )
    monkeypatch.setattr(
        append_brush_orders,
        "read_all_source_batches",
        lambda explicit_files=None: calls.append(f"read:{explicit_files == [source_file]}") or [],
    )

    append_brush_orders.run(dry_run=True, auto_fetch_wechat=True, wechat_month_day=(6, 1))

    assert calls == ["find:6-1", "read:True"]


def test_main_routes_to_workflow_without_direct_append(monkeypatch) -> None:
    # 薄 wrapper 只透传参数给 workflow：结构上不 import 追加/run 能力，故无从直连。
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["append_brush_orders", "--dry-run", "昨天的"])
    monkeypatch.setattr(task_entry, "_run_workflow", lambda args: calls.append(list(args)) or 0, raising=False)

    assert task_entry.main() == 0
    assert calls == [["append_brush_orders", "--dry-run", "昨天的"]]
    assert not hasattr(task_entry, "run")
    assert not hasattr(task_entry, "configure_paths")


def test_wechat_source_label_tolerates_wechat_renamed_suffix(tmp_path) -> None:
    total_file = tmp_path / "【总表】天猫超市6.1账2317.9元5单(1)(1).xlsx"
    tang_file = tmp_path / "奥克斯索隆6🈷️1(1).xlsx"
    total_file.write_text("", encoding="utf-8")
    tang_file.write_text("", encoding="utf-8")

    assert append_brush_orders.matched_wechat_source_label(total_file, 6, 1) == "天猫超市总表"
    assert append_brush_orders.matched_wechat_source_label(tang_file, 6, 1) == "奥克斯索隆"
