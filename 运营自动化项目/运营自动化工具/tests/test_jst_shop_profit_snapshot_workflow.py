from __future__ import annotations

import json
from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow


def test_workflow_registers() -> None:
    wf = discover_workflow("jst_shop_profit_snapshot")

    assert wf.id == "jst_shop_profit_snapshot"
    assert [step.id for step in wf.steps] == [
        "check_inputs",
        "fetch_profit_detail",
        "write_snapshot",
        "collect_outputs",
    ]


def test_writes_yesterday_profit_detail_snapshot(monkeypatch, tmp_path: Path) -> None:
    from workflows.jst_shop_profit_snapshot import steps

    seen_commands: list[tuple[list[str], bool | None]] = []

    def fake_run_ops_json(command, interactive_recovery=None):
        seen_commands.append((list(command), interactive_recovery))
        return {
            "success": True,
            "data": {
                "date": "2026-06-17",
                "store": "（猫超）福安市启明工贸有限公司（肖国清）",
                "profit": 9393.03,
                "metric_field": "经营利润",
                "metrics": [
                    {"name": "销售收入", "value": 31001.77, "raw_value": "31001.77"},
                    {"name": "经营利润", "value": 9393.03, "raw_value": "9393.03"},
                ],
                "raw_response": {"code": 0, "data": {"summaryData": {"dayList": []}}},
            },
        }

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)

    output = tmp_path / "profit.json"
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        discover_workflow("jst_shop_profit_snapshot"),
        inputs={"args": ["--output", str(output)]},
        dry_run=False,
    )

    assert run.status == "success"
    assert seen_commands == [(["--json", "jst", "profit", "yesterday", "--detail"], True)]
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["period"] == "yesterday"
    assert payload["profit"] == 9393.03
    assert payload["metrics"][0]["name"] == "销售收入"
    assert payload["raw_response"]["code"] == 0
    assert any(artifact.role == "profit_snapshot" and artifact.path == str(output) for artifact in run.artifacts)


def test_month_snapshot_uses_month_profit_detail(monkeypatch, tmp_path: Path) -> None:
    from workflows.jst_shop_profit_snapshot import steps

    seen_commands: list[list[str]] = []

    def fake_run_ops_json(command, interactive_recovery=None):
        seen_commands.append(list(command))
        return {
            "success": True,
            "data": {
                "month": "2026-06",
                "store": "（猫超）福安市启明工贸有限公司（肖国清）",
                "profit": 12345.67,
                "metric_field": "经营利润",
                "metrics": [{"name": "经营利润", "value": 12345.67}],
            },
        }

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)

    output = tmp_path / "month_profit.json"
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        discover_workflow("jst_shop_profit_snapshot"),
        inputs={"args": ["--month", "2026-06", "--output", str(output)]},
        dry_run=False,
    )

    assert run.status == "success"
    assert seen_commands == [["--json", "jst", "profit", "month", "--month", "2026-06", "--detail"]]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["period"] == "month"
    assert payload["month"] == "2026-06"


def test_day_snapshot_uses_day_profit_detail(monkeypatch, tmp_path: Path) -> None:
    from workflows.jst_shop_profit_snapshot import steps

    seen_commands: list[list[str]] = []

    def fake_run_ops_json(command, interactive_recovery=None):
        seen_commands.append(list(command))
        return {
            "success": True,
            "data": {
                "date": "2026-06-15",
                "store": "（猫超）福安市启明工贸有限公司（肖国清）",
                "profit": 678.90,
                "metric_field": "经营利润",
                "metrics": [{"name": "经营利润", "value": 678.90}],
            },
        }

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)

    output = tmp_path / "day_profit.json"
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        discover_workflow("jst_shop_profit_snapshot"),
        inputs={"args": ["--date", "2026-06-15", "--output", str(output)]},
        dry_run=False,
    )

    assert run.status == "success"
    assert seen_commands == [["--json", "jst", "profit", "day", "--date", "2026-06-15", "--detail"]]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["period"] == "day"
    assert payload["date"] == "2026-06-15"


def test_date_and_month_are_mutually_exclusive(monkeypatch, tmp_path: Path) -> None:
    from workflows.jst_shop_profit_snapshot import steps

    def fail_if_called(command, interactive_recovery=None):  # pragma: no cover - 不应被调用
        raise AssertionError("互斥校验失败时不应调用 Ops-Cli")

    monkeypatch.setattr(steps, "run_ops_json", fail_if_called)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        discover_workflow("jst_shop_profit_snapshot"),
        inputs={"args": ["--date", "2026-06-15", "--month", "2026-06"]},
        dry_run=True,
    )

    assert run.status == "failed"
    assert any("互斥" in err for step in run.steps for err in (step.errors or []))


def test_financial_summary_and_metrics_selection(monkeypatch, tmp_path: Path) -> None:
    from workflows.jst_shop_profit_snapshot import steps

    def fake_run_ops_json(command, interactive_recovery=None):
        return {
            "success": True,
            "data": {
                "month": "2026-06",
                "store": "（猫超）启明",
                "profit": 32353.25,
                "metric_field": "经营利润",
                "metrics": [
                    {"name": "销售收入", "value": 172654.38, "percent": 100.0},
                    {"name": "毛利额", "value": 70568.58, "percent": 40.87},
                    {"name": "6601：销售费用", "value": 14054.3, "percent": 8.14},
                    {"name": "660101：营销费用", "value": 6713.6, "percent": 3.89},
                    {"name": "660101020：淘系-万相台关键词推广", "value": 1878.83, "percent": 1.09},
                    {"name": "6604：财务费用", "value": 7769.44, "percent": 4.5},
                    {"name": "经营利润", "value": 32353.25, "percent": 18.74},
                ],
            },
        }

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)

    output = tmp_path / "month.json"
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        discover_workflow("jst_shop_profit_snapshot"),
        inputs={"args": ["--month", "2026-06", "--output", str(output), "--metrics", "营销费用,财务费用,万相台"]},
        dry_run=False,
    )

    assert run.status == "success"
    summary = {row["label"]: row["value"] for row in run.outputs["financial_summary"]}
    # 编码精确匹配：6601 销售费用 与 660101 营销费用 不串扰
    assert summary["销售费用"] == 14054.3
    assert summary["营销费用"] == 6713.6
    assert summary["财务费用"] == 7769.44
    assert summary["经营利润"] == 32353.25
    # --metrics 按名称挑选（含层级叶子项）
    selected = {row["matched"]: row["name"] for row in run.outputs["selected_metrics"]}
    assert selected["营销费用"] == "660101：营销费用"
    assert selected["财务费用"] == "6604：财务费用"
    assert selected["万相台"] == "660101020：淘系-万相台关键词推广"
    # 写出的快照 JSON 也含 financial_summary（增量，不破坏原有字段）
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert any(r["label"] == "营销费用" and r["value"] == 6713.6 for r in payload["financial_summary"])
    assert payload["metrics"][0]["name"] == "销售收入"  # 原有 metrics 原样保留


def test_kpi_summary_full_flag_and_metric_kpi_selection(monkeypatch, tmp_path: Path) -> None:
    from workflows.jst_shop_profit_snapshot import steps

    def fake_run_ops_json(command, interactive_recovery=None):
        return {
            "success": True,
            "data": {
                "month": "2026-06",
                "store": "（猫超）启明",
                "profit": 32353.25,
                "metric_field": "经营利润",
                "metrics": [
                    {"name": "销售收入", "value": 172654.38, "percent": 100.0},
                    {"name": "660101：营销费用", "value": 6713.6, "percent": 3.89},
                    {"name": "经营利润", "value": 32353.25, "percent": 18.74},
                ],
                "raw_data": {
                    "summaryData": {
                        "grossProfitRate": 45.54,
                        "refundrateAfter": 19.02,
                        "billQuantity": 617.0,
                        "avgBillSalePrice": 369.71,
                        "grossProfitRateByReturn": 0.0,  # 退货后系列：不应进 kpi_summary
                    }
                },
            },
        }

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)

    output = tmp_path / "month.json"
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        discover_workflow("jst_shop_profit_snapshot"),
        inputs={"args": ["--month", "2026-06", "--output", str(output), "--full", "--metrics", "毛利率,客单价,营销费用"]},
        dry_run=False,
    )

    assert run.status == "success"
    # kpi_summary 默认就有，含运营 KPI、不含「退货后/ByReturn」
    kpis = {row["label"]: row["value"] for row in run.outputs["kpi_summary"]}
    assert kpis["毛利率"] == 45.54
    assert kpis["客单价"] == 369.71
    assert kpis["单量"] == 617.0
    assert all("退货后" not in label for label in kpis)
    # --full：outputs 带完整 metrics
    assert [m["name"] for m in run.outputs["metrics"]] == ["销售收入", "660101：营销费用", "经营利润"]
    # --metrics 跨 KPI 与利润科目混合命中
    picked = {row["matched"]: (row["value"], row.get("kind")) for row in run.outputs["selected_metrics"]}
    assert picked["毛利率"] == (45.54, "kpi")
    assert picked["客单价"] == (369.71, "kpi")
    assert picked["营销费用"][0] == 6713.6  # 利润科目，无 kind
    # 快照 JSON 也含 kpi_summary（增量）
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert any(r["label"] == "毛利率" and r["value"] == 45.54 for r in payload["kpi_summary"])


def test_dry_run_does_not_call_ops_cli(monkeypatch, tmp_path: Path) -> None:
    from workflows.jst_shop_profit_snapshot import steps

    def fail_if_called(command, interactive_recovery=None):
        raise AssertionError("dry-run 不应请求 Ops-Cli")

    monkeypatch.setattr(steps, "run_ops_json", fail_if_called)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        discover_workflow("jst_shop_profit_snapshot"),
        inputs={"args": ["--dry-run"]},
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert run.outputs["period"] == "yesterday"
    assert run.outputs["written"] is False
    assert run.outputs["planned"] is True
