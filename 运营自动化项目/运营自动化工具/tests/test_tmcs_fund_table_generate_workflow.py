from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from openpyxl import load_workbook
from PIL import Image

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from core.task_registry import resolve_task
from workflows.tmcs_fund_table_generate import steps
from workflows.tmcs_fund_table_generate.workflow import build_workflow


def _png(path: Path, color: str) -> Path:
    image = Image.new("RGB", (240, 120), color=color)
    image.save(path)
    return path


def _step_outputs(runner: WorkflowRunner, step_id: str) -> dict:
    path = runner.last_run_dir / "steps" / f"{step_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["outputs"]


def test_workflow_registers() -> None:
    workflow = discover_workflow("tmcs_fund_table_generate")
    assert workflow.id == "tmcs_fund_table_generate"
    assert [step.id for step in workflow.steps] == [
        "check_inputs",
        "fetch_receivable_amount",
        "fetch_promotion_balance",
        "validate_amounts",
        "generate_fund_table",
        "verify_generated_excel",
        "collect_outputs",
    ]


def test_chinese_alias_resolves() -> None:
    assert resolve_task("猫超资金表生成") == "tmcs_fund_table_generate"
    assert resolve_task("天猫超市资金表生成") == "tmcs_fund_table_generate"
    assert resolve_task("生成猫超资金表") == "tmcs_fund_table_generate"


def test_workflow_generates_excel_and_records_artifact(tmp_path: Path, monkeypatch) -> None:
    receivable_shot = _png(tmp_path / "receivable.png", "white")
    promotion_shot = _png(tmp_path / "promotion.png", "blue")
    calls: list[tuple[list[str], bool | None]] = []

    def fake_run_ops_json(command, interactive_recovery=None):
        calls.append((list(command), interactive_recovery))
        if command[2:5] == ["fund", "receivable-bill", "sum"]:
            return {
                "success": True,
                "platform": "tmcs",
                "command": "fund receivable-bill sum",
                "data": {
                    "month": "2026-05",
                    "total_amount": 802.35,
                    "amounts": [123.45, 678.9],
                    "screenshot_path": str(receivable_shot),
                    "source": "page",
                    "simulated": False,
                },
            }
        return {
            "success": True,
            "platform": "tmcs",
            "command": "fund promotion-balance sum",
            "data": {
                "total_amount": 600.0,
                "balances": {"jubao_pen": 100.0, "zhiduoxing": 200.0, "wanxiangtai": 300.0},
                "screenshot_path": str(promotion_shot),
                "source": "page",
                "simulated": False,
            },
        }

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)
    runner = WorkflowRunner(tmp_path / "runs")
    output = tmp_path / "猫超资金表_2026-05.xlsx"
    run = runner.run(
        build_workflow(),
        inputs={
            "dry_run": True,
            "args": [
                "--dry-run",
                "--month",
                "2026-05",
                "--reserve-balance",
                "123.45",
                "--bank-card-balance",
                "678.90",
                "--output-file",
                str(output),
                "--output-dir",
                str(tmp_path),
            ],
            "month": "2026-05",
        },
        dry_run=True,
    )

    assert run.status == "dry_run_success"
    assert output.is_file()
    assert calls[0][0][:5] == ["--json", "tmcs", "fund", "receivable-bill", "sum"]
    assert calls[1][0][:5] == ["--json", "tmcs", "fund", "promotion-balance", "sum"]
    assert "--dry-run" in calls[0][0]
    assert calls[0][1] is False
    assert calls[1][1] is False
    out = _step_outputs(runner, "collect_outputs")
    assert out["month"] == "2026-05"
    assert out["receivable_amount"] == 802.35
    assert out["promotion_balance"] == 600.0
    assert out["reserve_balance"] == 123.45
    assert out["bank_card_balance"] == 678.9
    assert out["formula_check_result"] == {"Q2": True, "S2": True}
    assert run.artifacts[0].path == str(output)
    assert run.artifacts[0].metadata["reserve_balance"] == 123.45
    assert run.artifacts[0].metadata["bank_card_balance"] == 678.9
    workbook = load_workbook(output)
    sheet = workbook["店铺资金"]
    assert sheet["M2"].value == 123.45
    assert sheet["N2"].value == 678.9
    workbook.close()


def test_negative_amount_fails_validation(tmp_path: Path, monkeypatch) -> None:
    shot = _png(tmp_path / "shot.png", "white")

    def fake_run_ops_json(command, interactive_recovery=None):
        amount = -1 if command[2:5] == ["fund", "receivable-bill", "sum"] else 1
        return {"success": True, "platform": "tmcs", "command": "x", "data": {"total_amount": amount, "screenshot_path": str(shot)}}

    monkeypatch.setattr(steps, "run_ops_json", fake_run_ops_json)
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(
        build_workflow(),
        inputs={"dry_run": True, "args": ["--dry-run", "--month", "2026-05", "--output-dir", str(tmp_path)]},
        dry_run=True,
    )

    assert run.status == "failed"
    assert any("金额不能为负" in err for err in run.errors)


def test_default_output_dir_uses_configured_desktop(monkeypatch, tmp_path: Path) -> None:
    desktop = tmp_path / "Desktop"
    desktop.mkdir()

    def fake_get_path(name: str) -> Path:
        if name != "desktop_dir":
            raise KeyError(name)
        return desktop

    monkeypatch.setattr(steps, "get_path", fake_get_path, raising=False)
    ctx = SimpleNamespace(inputs={"args": ["--month", "2026-05"]}, dry_run=True, state={})

    result = steps.check_inputs(ctx)

    assert result.success is True
    assert Path(result.outputs["output_file"]).parent == desktop
