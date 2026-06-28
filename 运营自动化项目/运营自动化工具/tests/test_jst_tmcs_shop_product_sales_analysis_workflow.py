from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from core.task_registry import resolve_task

from workflows.jst_tmcs_shop_product_sales_analysis import steps
from workflows.jst_tmcs_shop_product_sales_analysis.workflow import build_workflow

WORKFLOW_ID = "jst_tmcs_shop_product_sales_analysis"
NUM_COLS = 82


@pytest.fixture(autouse=True)
def desktop_dir(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "Desktop"
    path.mkdir(exist_ok=True)

    def fake_get_path(name: str) -> Path:
        if name != "desktop_dir":
            raise KeyError(name)
        return path

    monkeypatch.setattr(steps, "get_path", fake_get_path, raising=False)
    return path


def _build_sample_csv(path: Path) -> Path:
    header = [""] * NUM_COLS
    header[0] = "店铺款式编码"
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
    rows = []

    def row(sku, name, qty, amt, gross, profit, refund, ad):
        r = [""] * NUM_COLS
        r[0], r[3] = sku, name
        r[6], r[7], r[8] = str(qty), str(amt), "0"
        r[19], r[21], r[23] = str(gross), "0", "0"
        r[25], r[43], r[81] = str(profit), str(refund), str(ad)
        return r

    rows.append(row("AUX001", "奥克斯电饭煲", 20, 6000, 2000, 2000, 1, 0))
    rows.append(row("SUP002", "苏泊尔炒锅", 8, 2000, 600, 420, 1, 40))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for r in rows:
            writer.writerow(r)
    return path


def test_workflow_registers() -> None:
    wf = discover_workflow(WORKFLOW_ID)
    assert wf.id == WORKFLOW_ID
    assert [s.id for s in wf.steps] == [
        "check_inputs",
        "fetch_sales_csv",
        "analyze_sales_csv",
        "write_outputs",
        "collect_artifacts",
    ]


def test_chinese_entry_resolves() -> None:
    assert resolve_task("猫超店铺商品销售分析") == "猫超店铺商品销售分析"
    assert resolve_task("聚水潭商品销售分析") == "猫超店铺商品销售分析"
    assert resolve_task("聚水潭商品利润宝贝分析") == "猫超店铺商品销售分析"
    assert resolve_task("商品销售情况编码输出") == "猫超店铺商品销售分析"


def test_month_defaults_to_last_month(monkeypatch) -> None:
    import datetime as real_datetime

    class FakeDate(real_datetime.date):
        @classmethod
        def today(cls):
            return real_datetime.date(2026, 6, 3)

    monkeypatch.setattr(steps, "date", FakeDate)
    assert steps._last_month() == "2026-05"


def test_use_local_file_dry_run(monkeypatch, tmp_path: Path) -> None:
    csv_path = _build_sample_csv(tmp_path / "商品销售情况.csv")
    # dry-run + 本地文件不应触发任何 Ops-Cli 调用
    monkeypatch.setattr(
        steps,
        "run_ops_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("提供本地文件时不应请求 Ops-Cli")),
    )

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--use-local-file", str(csv_path), "--dry-run"]},
        dry_run=True,
    )
    assert run.status == "dry_run_success"
    analyze = json.loads((runner.last_run_dir / "steps" / "analyze_sales_csv.json").read_text(encoding="utf-8"))
    assert "AUX001" in analyze["outputs"]["style_codes"]
    assert "SUP002" in analyze["outputs"]["style_codes"]


def test_dry_run_without_local_file_does_not_download(monkeypatch, tmp_path: Path) -> None:
    seen: list = []

    def fake_ops(command, *a, **k):
        seen.append(list(command))
        return {"success": True, "data": {"simulated": True, "downloaded": False, "month": "2026-05", "csv_path": None}}

    monkeypatch.setattr(steps, "run_ops_json", fake_ops)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--month", "2026-05", "--dry-run"]},
        dry_run=True,
    )
    assert run.status == "dry_run_success"
    # 透传 dry-run，绝不带 --execute
    assert "--dry-run" in seen[0]
    assert "--execute" not in seen[0]
    analyze = json.loads((runner.last_run_dir / "steps" / "analyze_sales_csv.json").read_text(encoding="utf-8"))
    assert analyze["outputs"]["skipped"] is True


def test_days_range_is_translated_to_start_and_end_date(monkeypatch, tmp_path: Path) -> None:
    import datetime as real_datetime

    class FakeDate(real_datetime.date):
        @classmethod
        def today(cls):
            return real_datetime.date(2026, 6, 15)

    seen: list[list[str]] = []

    def fake_ops(command, *a, **k):
        seen.append(list(command))
        return {"success": True, "data": {"simulated": True, "downloaded": False, "csv_path": None}}

    monkeypatch.setattr(steps, "date", FakeDate)
    monkeypatch.setattr(steps, "run_ops_json", fake_ops)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--days", "7", "--dry-run"]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert "--start-date" in seen[0]
    assert seen[0][seen[0].index("--start-date") + 1] == "2026-06-09"
    assert seen[0][seen[0].index("--end-date") + 1] == "2026-06-15"
    assert "--month" not in seen[0]


def test_explicit_date_range_is_passed_to_ops(monkeypatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def fake_ops(command, *a, **k):
        seen.append(list(command))
        return {"success": True, "data": {"simulated": True, "downloaded": False, "csv_path": None}}

    monkeypatch.setattr(steps, "run_ops_json", fake_ops)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--start-date", "2026-06-01", "--end-date", "2026-06-15", "--dry-run"]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert "--start-date" in seen[0]
    assert seen[0][seen[0].index("--start-date") + 1] == "2026-06-01"
    assert seen[0][seen[0].index("--end-date") + 1] == "2026-06-15"
    assert "--month" not in seen[0]


def test_output_file_and_artifact(monkeypatch, tmp_path: Path) -> None:
    csv_path = _build_sample_csv(tmp_path / "商品销售情况.csv")
    output_path = tmp_path / "编码结果.csv"
    monkeypatch.setattr(steps, "run_ops_json", lambda *a, **k: {"success": True, "data": {}})

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--use-local-file", str(csv_path), "--output", str(output_path)]},
        dry_run=False,
    )
    assert run.status == "success"
    assert output_path.exists()
    content = output_path.read_text(encoding="utf-8-sig")
    assert "AUX001" in content

    artifacts = json.loads((runner.last_run_dir / "artifacts.json").read_text(encoding="utf-8"))
    roles = {a["role"] for a in artifacts}
    assert "sales_source" in roles
    assert "style_code_output" in roles


def test_xlsx_output_is_promotion_list(monkeypatch, tmp_path: Path) -> None:
    """--output xxx.xlsx 生成分档位推广清单（含商品名称/利润率/销量），而非单列编码。"""
    openpyxl = pytest.importorskip("openpyxl")
    csv_path = _build_sample_csv(tmp_path / "商品销售情况.csv")
    output_path = tmp_path / "推广清单.xlsx"
    monkeypatch.setattr(steps, "run_ops_json", lambda *a, **k: {"success": True, "data": {}})

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--use-local-file", str(csv_path), "--output", str(output_path)]},
        dry_run=False,
    )
    assert run.status == "success"
    assert output_path.exists()

    ws = openpyxl.load_workbook(output_path).active
    text = "\n".join(
        " ".join(str(c) for c in row if c is not None)
        for row in ws.iter_rows(values_only=True)
    )
    assert "优先推广" in text and "次级推广" in text
    assert "商品名称" in text and "利润率" in text and "销量(件)" in text
    assert "AUX001" in text and "奥克斯电饭煲" in text  # 编码 + 商品名称都在


def test_default_output_is_xlsx_on_desktop_when_not_specified(monkeypatch, tmp_path: Path, desktop_dir: Path) -> None:
    """不指定 --output 时，默认生成分档位 Excel 到 desktop_dir。"""
    pytest.importorskip("openpyxl")
    csv_path = _build_sample_csv(tmp_path / "商品销售情况.csv")
    monkeypatch.setattr(steps, "run_ops_json", lambda *a, **k: {"success": True, "data": {}})

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--use-local-file", str(csv_path), "--month", "2026-05"]},
        dry_run=False,
    )
    assert run.status == "success"
    default_file = desktop_dir / "猫超店铺推广清单_2026-05.xlsx"
    assert default_file.exists()


def test_ops_download_csv_deleted_after_output(monkeypatch, tmp_path: Path) -> None:
    """分析输出成功后，自动删除「我们下载的」原始 CSV（ops_export）。"""
    pytest.importorskip("openpyxl")
    downloaded = _build_sample_csv(tmp_path / "下载_商品销售情况.csv")
    output_path = tmp_path / "推广清单.xlsx"

    def fake_ops(command, *a, **k):
        return {"success": True, "data": {"csv_path": str(downloaded), "downloaded": True, "month": "2026-05"}}

    monkeypatch.setattr(steps, "run_ops_json", fake_ops)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--month", "2026-05", "--execute", "--output", str(output_path)]},
        dry_run=False,
    )
    assert run.status == "success"
    assert output_path.exists()
    assert not downloaded.exists()  # 原始下载 CSV 已被删除
    out = json.loads((runner.last_run_dir / "steps" / "collect_artifacts.json").read_text(encoding="utf-8"))
    assert out["outputs"]["source_csv_deleted"] is True


def test_use_local_file_not_deleted(monkeypatch, tmp_path: Path) -> None:
    """--use-local-file 指定的本地文件分析后绝不删除。"""
    pytest.importorskip("openpyxl")
    local = _build_sample_csv(tmp_path / "我的商品销售情况.csv")
    output_path = tmp_path / "推广清单.xlsx"
    monkeypatch.setattr(steps, "run_ops_json", lambda *a, **k: {"success": True, "data": {}})

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--use-local-file", str(local), "--output", str(output_path)]},
        dry_run=False,
    )
    assert run.status == "success"
    assert local.exists()  # 本地文件保留


def test_missing_style_code_field_fails_workflow(monkeypatch, tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    header = [""] * NUM_COLS
    header[0] = "款式编码"  # 缺少「店铺款式编码」
    with bad.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        r = [""] * NUM_COLS
        r[0] = "X"
        r[6], r[7] = "10", "3000"
        writer.writerow(r)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": False, "args": ["--use-local-file", str(bad)]},
        dry_run=False,
    )
    assert run.status == "failed"
    assert any("店铺款式编码" in err for err in run.errors)
