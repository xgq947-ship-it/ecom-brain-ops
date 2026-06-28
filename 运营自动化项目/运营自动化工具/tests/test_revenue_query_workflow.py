from __future__ import annotations

from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow


def test_workflow_registers() -> None:
    wf = discover_workflow("revenue_query")

    assert wf.id == "revenue_query"
    assert wf.name == "今日实时营业额"
    assert [step.id for step in wf.steps] == [
        "check_inputs",
        "fetch_order_stats",
        "collect_outputs",
    ]


def test_fetches_today_revenue_for_default_shop(monkeypatch, tmp_path: Path) -> None:
    from workflows.revenue_query import steps

    seen_commands: list[tuple[list[str], bool | None]] = []

    def fake_run_ops_json(command, interactive_recovery=None):
        seen_commands.append((list(command), interactive_recovery))
        return {
            "success": True,
            "data": {
                "date": "2026-06-18",
                "store": "（猫超）福安市启明工贸有限公司（肖国清）",
                "order_count": 160,
                "paid_amount": 71131.93,
                "metric_field": "已付款金额",
                "context_path": "/tmp/context.json",
            },
        }

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(discover_workflow("revenue_query"), inputs={"args": []}, dry_run=False)

    assert run.status == "success"
    assert seen_commands == [(["--json", "jst", "order", "stats", "--date", "today", "--shop", "qiming"], True)]
    assert run.outputs["date"] == "2026-06-18"
    assert run.outputs["shop"] == "qiming"
    assert run.outputs["paid_amount"] == 71131.93
    assert run.outputs["order_count"] == 160
    assert run.outputs["metric_field"] == "已付款金额"


def test_fetches_revenue_for_selected_shop_and_date(monkeypatch, tmp_path: Path) -> None:
    from workflows.revenue_query import steps

    seen_commands: list[list[str]] = []

    def fake_run_ops_json(command, interactive_recovery=None):
        seen_commands.append(list(command))
        return {
            "success": True,
            "data": {
                "date": "2026-06-16",
                "store": "苏泊尔迎众专卖店（曹林辉）",
                "order_count": 12,
                "paid_amount": 3456.78,
                "metric_field": "已付款金额",
            },
        }

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        discover_workflow("revenue_query"),
        inputs={"args": ["--shop", "subor", "--date", "2026-06-16"]},
        dry_run=False,
    )

    assert run.status == "success"
    assert seen_commands == [["--json", "jst", "order", "stats", "--date", "2026-06-16", "--shop", "subor"]]
    assert run.outputs["shop"] == "subor"
    assert run.outputs["store"] == "苏泊尔迎众专卖店（曹林辉）"
    assert run.outputs["paid_amount"] == 3456.78


def test_dry_run_does_not_call_ops_cli(monkeypatch, tmp_path: Path) -> None:
    from workflows.revenue_query import steps

    def fail_if_called(command, interactive_recovery=None):
        raise AssertionError("dry-run 不应请求 Ops-Cli")

    monkeypatch.setattr(steps, "run_ops_json", fail_if_called)

    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(discover_workflow("revenue_query"), inputs={"args": ["--dry-run"]}, dry_run=True)

    assert run.status == "dry_run_success"
    assert run.outputs["planned"] is True
    assert run.outputs["date"] == "today"
    assert run.outputs["shop"] == "qiming"
