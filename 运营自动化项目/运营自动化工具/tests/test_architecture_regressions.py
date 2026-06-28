from __future__ import annotations

from pathlib import Path

from core.runtime import WorkflowRunner
from workflows.jst_tmcs_shop_product_sales_analysis.workflow import build_workflow


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent


def test_workflow_rejects_unknown_arguments(tmp_path: Path) -> None:
    runner = WorkflowRunner(tmp_path / "runs")

    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--typo-output", "x"]},
        dry_run=True,
    )

    assert run.status == "failed"
    assert any("未知参数" in err and "--typo-output" in err for err in run.errors)


def test_workflow_steps_do_not_parse_unknown_args_silently() -> None:
    offenders = [
        path.relative_to(REPO_ROOT)
        for path in (ROOT / "workflows").glob("*/steps.py")
        if "parse_known_args" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_runtime_code_has_no_user_specific_absolute_paths() -> None:
    targets = [
        ROOT / "workflows" / "tmcs_fund_table_generate" / "steps.py",
        ROOT / "workflows" / "tmcs_realtime_inventory_watch" / "steps.py",
        ROOT / "workflows" / "jst_massage_chair_order_remark" / "steps.py",
        ROOT / "workflows" / "tmcs_sync_jst_shop_goods" / "sync_config.py",
        ROOT / "workflows" / "append_brush_orders" / "appender.py",
        REPO_ROOT / "Ops-Cli" / "src" / "ops_cli" / "config.py",
    ]
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in targets
        if "/Users/" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_complex_workflows_use_typed_state() -> None:
    targets = [
        ROOT / "workflows" / "jst_massage_chair_order_remark" / "steps.py",
        ROOT / "workflows" / "tmcs_priority_promotion_plan_create" / "steps.py",
    ]

    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in targets
        if 'ctx.state["' in path.read_text(encoding="utf-8") or "ctx.state.get(" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []
