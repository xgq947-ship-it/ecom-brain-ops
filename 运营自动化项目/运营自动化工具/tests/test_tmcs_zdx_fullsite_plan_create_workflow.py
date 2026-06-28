"""Tests for tmcs_zdx_fullsite_plan_create workflow."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from core.task_registry import TASK_ALIASES
from workflows.tmcs_sku_roi.excel_lookup import _parse_control_price
from workflows.tmcs_zdx_fullsite_plan_create import steps
from workflows.tmcs_zdx_fullsite_plan_create.workflow import build_workflow


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_tmcs_file(path: Path, *, product_code: str = "111222333", barcode: str = "BAR001") -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(["商品编码", "商品名称", "商品上下架状态", "SKU编码", "SKU上下架状态", "生产厂家", "条码"])
    ws.append([product_code, "测试商品", "上架", "SKU001", "上架", "厂商", barcode])
    wb.save(path)
    wb.close()
    return path


def _make_jst_file(path: Path, *, product_code: str = "BAR001", price: str = "99", cost: float = 40.0) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(["市场|吊牌价", "基本售价", "图片", "款式编码", "商品编码", "商品名称", "商品简称",
               "颜色及规格", "颜色", "规格", "实际库存数", "订单占有数", "淘系控价", "成本价"])
    ws.append([None, None, None, "S001", product_code, "测试商品", None, None, None, None, 0, 0, price, cost])
    wb.save(path)
    wb.close()
    return path


def _make_roi_config(path: Path) -> Path:
    payload = {
        "supply_price_factor": 0.9,
        "vip_discount_rate": 0.0,
        "general_fee_rate": 0.007,
        "other_fee_rate": 0.02,
        "storage_fee_rate": 0.0,
        "tax_rate": 0.045,
        "management_fee_rate": 0.048,
        "refund_rate": 0.1,
        "refund_flat_fee": 5.0,
        "domestic_shipping_fee": 5.0,
        "gift_cost": 0.0,
        "safe_profit_rate": 0.1,
        "ideal_promotion_ratio": 0.12,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _make_runner(tmp_path: Path) -> WorkflowRunner:
    return WorkflowRunner(tmp_path / "runs")


def _run(runner: WorkflowRunner, args: list[str], *, dry_run: bool = True) -> object:
    workflow = build_workflow()
    return runner.run(workflow, inputs={"args": args}, dry_run=dry_run)


def _is_success(result) -> bool:
    return result.status in ("success", "dry_run_success")


# ---------------------------------------------------------------------------
# 1. workflow 可以注册
# ---------------------------------------------------------------------------

def test_workflow_can_be_discovered() -> None:
    workflow = discover_workflow("tmcs_zdx_fullsite_plan_create")
    assert workflow.id == "tmcs_zdx_fullsite_plan_create"
    assert len(workflow.steps) == 6


# ---------------------------------------------------------------------------
# 2. 中文入口可以解析
# ---------------------------------------------------------------------------

def test_chinese_aliases_registered() -> None:
    assert "创建智多星全站推广计划" in TASK_ALIASES
    assert "智多星全站推广" in TASK_ALIASES
    assert "创建货品全站推" in TASK_ALIASES
    assert "货品全站推创建计划" in TASK_ALIASES


# ---------------------------------------------------------------------------
# 3. plan_name 默认生成符合规则
# ---------------------------------------------------------------------------

def test_plan_name_default_generation(tmp_path) -> None:
    runner = _make_runner(tmp_path)

    with (
        patch.object(steps, "_DEFAULT_TMCS_FILE", tmp_path / "tmcs.xlsx"),
        patch.object(steps, "_DEFAULT_JST_FILE", tmp_path / "jst.xlsx"),
        patch.object(steps, "_DEFAULT_ROI_CONFIG_FILE", tmp_path / "roi.json"),
        patch("workflows.tmcs_zdx_fullsite_plan_create.steps.run_ops_json") as mock_ops,
    ):
        mock_ops.return_value = {
            "success": True, "platform": "tmcs", "command": "zdx fullsite-plan create",
            "data": {"executed": False, "created": False, "simulated": True, "dry_run": True,
                     "context_path": str(tmp_path / "ctx.json")},
        }
        _make_tmcs_file(tmp_path / "tmcs.xlsx", product_code="123456789")
        _make_jst_file(tmp_path / "jst.xlsx")
        _make_roi_config(tmp_path / "roi.json")

        result = _run(runner, ["--item-id", "123456789", "--daily-budget", "100"], dry_run=True)

    today = date.today().strftime("%m%d")
    assert _is_success(result), result.errors
    step_outputs = {s.step_id: s.outputs for s in result.steps}
    assert step_outputs["build_plan_name"]["plan_name"] == f"全站推广_123456789_{today}"


# ---------------------------------------------------------------------------
# 4. daily_budget <= 0 报错
# ---------------------------------------------------------------------------

def test_daily_budget_zero_fails(tmp_path) -> None:
    runner = _make_runner(tmp_path)
    result = _run(runner, ["--item-id", "123", "--daily-budget", "0"], dry_run=True)
    assert result.status == "failed"
    assert any("daily-budget" in (e or "") for e in result.errors)


def test_daily_budget_negative_fails(tmp_path) -> None:
    runner = _make_runner(tmp_path)
    result = _run(runner, ["--item-id", "123", "--daily-budget", "-50"], dry_run=True)
    assert result.status == "failed"


# ---------------------------------------------------------------------------
# 5. 没有 item_id 报错
# ---------------------------------------------------------------------------

def test_missing_item_id_fails(tmp_path) -> None:
    runner = _make_runner(tmp_path)
    result = _run(runner, ["--daily-budget", "100"], dry_run=True)
    assert result.status == "failed"
    assert any("item-id" in (e or "") for e in result.errors)


# ---------------------------------------------------------------------------
# 6. 没有 --roi 时会调用 excel_lookup 路径
# ---------------------------------------------------------------------------

def test_roi_resolved_from_excel(tmp_path) -> None:
    runner = _make_runner(tmp_path)

    with (
        patch.object(steps, "_DEFAULT_TMCS_FILE", tmp_path / "tmcs.xlsx"),
        patch.object(steps, "_DEFAULT_JST_FILE", tmp_path / "jst.xlsx"),
        patch.object(steps, "_DEFAULT_ROI_CONFIG_FILE", tmp_path / "roi.json"),
        patch("workflows.tmcs_zdx_fullsite_plan_create.steps.run_ops_json") as mock_ops,
    ):
        mock_ops.return_value = {
            "success": True, "platform": "tmcs", "command": "zdx fullsite-plan create",
            "data": {"executed": False, "created": False, "simulated": True, "dry_run": True,
                     "context_path": str(tmp_path / "ctx.json")},
        }
        _make_tmcs_file(tmp_path / "tmcs.xlsx", product_code="111", barcode="BAR001")
        _make_jst_file(tmp_path / "jst.xlsx", product_code="BAR001", price="99", cost=40.0)
        _make_roi_config(tmp_path / "roi.json")

        result = _run(runner, ["--item-id", "111", "--daily-budget", "100"], dry_run=True)

    assert _is_success(result), result.errors
    step_outputs = {s.step_id: s.outputs for s in result.steps}
    assert step_outputs["resolve_target_roi"]["roi_source"] == "tmcs_sku_roi"
    assert step_outputs["resolve_target_roi"]["target_roi"] > 0


# ---------------------------------------------------------------------------
# 7. dry-run 下不传 --execute 给 Ops-Cli
# ---------------------------------------------------------------------------

def test_dry_run_does_not_pass_execute_to_ops(tmp_path) -> None:
    runner = _make_runner(tmp_path)

    with (
        patch.object(steps, "_DEFAULT_TMCS_FILE", tmp_path / "tmcs.xlsx"),
        patch.object(steps, "_DEFAULT_JST_FILE", tmp_path / "jst.xlsx"),
        patch.object(steps, "_DEFAULT_ROI_CONFIG_FILE", tmp_path / "roi.json"),
        patch("workflows.tmcs_zdx_fullsite_plan_create.steps.run_ops_json") as mock_ops,
    ):
        mock_ops.return_value = {
            "success": True, "platform": "tmcs", "command": "zdx fullsite-plan create",
            "data": {"executed": False, "created": False, "simulated": True, "dry_run": True,
                     "context_path": str(tmp_path / "ctx.json")},
        }
        _make_tmcs_file(tmp_path / "tmcs.xlsx", product_code="222")
        _make_jst_file(tmp_path / "jst.xlsx", product_code="BAR001")
        _make_roi_config(tmp_path / "roi.json")

        _run(runner, ["--item-id", "222", "--daily-budget", "100"], dry_run=True)

    called_args = mock_ops.call_args[0][0]
    assert "--dry-run" in called_args
    assert "--execute" not in called_args


# ---------------------------------------------------------------------------
# 8. 没有 --execute 不会真实创建计划
# ---------------------------------------------------------------------------

def test_no_execute_prevents_real_create(tmp_path) -> None:
    runner = _make_runner(tmp_path)
    # dry_run=False 但不传 --execute
    result = _run(runner, ["--item-id", "123", "--daily-budget", "100"], dry_run=False)
    assert result.status == "failed"
    assert any("--execute" in (e or "") for e in result.errors)


# ---------------------------------------------------------------------------
# 9. --execute 但缺少 --confirm-plan-name 报错
# ---------------------------------------------------------------------------

def test_execute_without_confirm_plan_name_fails(tmp_path) -> None:
    runner = _make_runner(tmp_path)
    result = _run(
        runner,
        ["--item-id", "123", "--daily-budget", "100", "--execute"],
        dry_run=False,
    )
    assert result.status == "failed"
    assert any("confirm-plan-name" in (e or "") for e in result.errors)


# ---------------------------------------------------------------------------
# 10. confirm_plan_name 不匹配时报错
# ---------------------------------------------------------------------------

def test_confirm_plan_name_mismatch_fails(tmp_path) -> None:
    runner = _make_runner(tmp_path)
    result = _run(
        runner,
        ["--item-id", "123", "--daily-budget", "100", "--execute",
         "--plan-name", "全站推广_123_0602", "--confirm-plan-name", "全站推广_123_9999"],
        dry_run=False,
    )
    assert result.status == "failed"
    assert any("不匹配" in (e or "") for e in result.errors)


# ---------------------------------------------------------------------------
# 11. Ops-Cli dry-run 返回 created=false
# ---------------------------------------------------------------------------

def test_ops_cli_dry_run_created_false(tmp_path) -> None:
    runner = _make_runner(tmp_path)

    with (
        patch.object(steps, "_DEFAULT_TMCS_FILE", tmp_path / "tmcs.xlsx"),
        patch.object(steps, "_DEFAULT_JST_FILE", tmp_path / "jst.xlsx"),
        patch.object(steps, "_DEFAULT_ROI_CONFIG_FILE", tmp_path / "roi.json"),
        patch("workflows.tmcs_zdx_fullsite_plan_create.steps.run_ops_json") as mock_ops,
    ):
        mock_ops.return_value = {
            "success": True, "platform": "tmcs", "command": "zdx fullsite-plan create",
            "data": {"executed": False, "created": False, "simulated": True, "dry_run": True,
                     "context_path": str(tmp_path / "ctx.json")},
        }
        _make_tmcs_file(tmp_path / "tmcs.xlsx", product_code="333")
        _make_jst_file(tmp_path / "jst.xlsx", product_code="BAR001")
        _make_roi_config(tmp_path / "roi.json")

        result = _run(runner, ["--item-id", "333", "--daily-budget", "100"], dry_run=True)

    assert _is_success(result), result.errors
    step_outputs = {s.step_id: s.outputs for s in result.steps}
    assert step_outputs["collect_outputs"]["created"] is False


# ---------------------------------------------------------------------------
# 12. Ops-Cli execute mock 返回 created=true
# ---------------------------------------------------------------------------

def test_ops_cli_execute_mock_created_true(tmp_path) -> None:
    runner = _make_runner(tmp_path)
    today = date.today().strftime("%m%d")
    plan_name = f"全站推广_444_{today}"

    with (
        patch.object(steps, "_DEFAULT_TMCS_FILE", tmp_path / "tmcs.xlsx"),
        patch.object(steps, "_DEFAULT_JST_FILE", tmp_path / "jst.xlsx"),
        patch.object(steps, "_DEFAULT_ROI_CONFIG_FILE", tmp_path / "roi.json"),
        patch("workflows.tmcs_zdx_fullsite_plan_create.steps.run_ops_json") as mock_ops,
    ):
        mock_ops.return_value = {
            "success": True, "platform": "tmcs", "command": "zdx fullsite-plan create",
            "data": {"executed": True, "created": True, "simulated": False, "dry_run": False,
                     "platform_plan_id": "PLAN_001",
                     "context_path": str(tmp_path / "ctx.json")},
        }
        _make_tmcs_file(tmp_path / "tmcs.xlsx", product_code="444")
        _make_jst_file(tmp_path / "jst.xlsx", product_code="BAR001")
        _make_roi_config(tmp_path / "roi.json")

        result = _run(
            runner,
            ["--item-id", "444", "--daily-budget", "100", "--execute",
             "--confirm-plan-name", plan_name],
            dry_run=False,
        )

    assert _is_success(result), result.errors
    step_outputs = {s.step_id: s.outputs for s in result.steps}
    assert step_outputs["collect_outputs"]["created"] is True

    called_args = mock_ops.call_args[0][0]
    assert "--execute" in called_args
    assert "--dry-run" not in called_args


# ---------------------------------------------------------------------------
# 13. ROI 获取失败时不创建计划
# ---------------------------------------------------------------------------

def test_roi_failure_prevents_create(tmp_path) -> None:
    runner = _make_runner(tmp_path)

    with (
        patch.object(steps, "_DEFAULT_TMCS_FILE", tmp_path / "tmcs.xlsx"),
        patch.object(steps, "_DEFAULT_JST_FILE", tmp_path / "jst.xlsx"),
        patch.object(steps, "_DEFAULT_ROI_CONFIG_FILE", tmp_path / "roi.json"),
        patch("workflows.tmcs_zdx_fullsite_plan_create.steps.run_ops_json") as mock_ops,
    ):
        # 猫超商品列表中不包含此商品ID
        _make_tmcs_file(tmp_path / "tmcs.xlsx", product_code="OTHER_CODE")
        _make_jst_file(tmp_path / "jst.xlsx")
        _make_roi_config(tmp_path / "roi.json")

        result = _run(runner, ["--item-id", "NONEXISTENT", "--daily-budget", "100"], dry_run=True)

    assert result.status == "failed"
    assert any("ROI_NOT_FOUND" in (e or "") for e in result.errors)
    mock_ops.assert_not_called()


# ---------------------------------------------------------------------------
# 14. --roi 手动传入时跳过 excel 查询
# ---------------------------------------------------------------------------

def test_manual_roi_skips_excel_lookup(tmp_path) -> None:
    runner = _make_runner(tmp_path)

    with (
        patch.object(steps, "_DEFAULT_TMCS_FILE", tmp_path / "does_not_exist.xlsx"),
        patch.object(steps, "_DEFAULT_JST_FILE", tmp_path / "does_not_exist.xlsx"),
        patch.object(steps, "_DEFAULT_ROI_CONFIG_FILE", tmp_path / "does_not_exist.json"),
        patch("workflows.tmcs_zdx_fullsite_plan_create.steps.run_ops_json") as mock_ops,
    ):
        mock_ops.return_value = {
            "success": True, "platform": "tmcs", "command": "zdx fullsite-plan create",
            "data": {"executed": False, "created": False, "simulated": True, "dry_run": True,
                     "context_path": str(tmp_path / "ctx.json")},
        }
        result = _run(
            runner, ["--item-id", "555", "--daily-budget", "100", "--roi", "3.5"],
            dry_run=True,
        )

    assert _is_success(result), result.errors
    step_outputs = {s.step_id: s.outputs for s in result.steps}
    assert step_outputs["resolve_target_roi"]["roi_source"] == "manual"
    assert step_outputs["resolve_target_roi"]["target_roi"] == 3.5


# ---------------------------------------------------------------------------
# 15. 淘系控价多值：默认报错，max 策略取最高价（含 _x000A_ 字面量）
# ---------------------------------------------------------------------------

def test_control_price_single_value() -> None:
    assert _parse_control_price("298") == 298.0


def test_control_price_multi_default_errors() -> None:
    # 真实换行
    with pytest.raises(ValueError, match="存在多个值"):
        _parse_control_price("268\n298")
    # openpyxl 未解码的 OOXML 转义字面量
    with pytest.raises(ValueError, match="存在多个值"):
        _parse_control_price("268_x000A_298")


def test_control_price_multi_max_takes_highest() -> None:
    assert _parse_control_price("268\n298", multi_price_strategy="max") == 298.0
    assert _parse_control_price("268_x000A_298", multi_price_strategy="max") == 298.0
    assert _parse_control_price("298_x000A_268", multi_price_strategy="max") == 298.0


def test_zdx_auto_roi_uses_max_price_on_multi(tmp_path) -> None:
    """全站推自动 ROI：控价多值时按最高价测算，不因多值中止。"""
    runner = _make_runner(tmp_path)
    with (
        patch.object(steps, "_DEFAULT_TMCS_FILE", tmp_path / "tmcs.xlsx"),
        patch.object(steps, "_DEFAULT_JST_FILE", tmp_path / "jst.xlsx"),
        patch.object(steps, "_DEFAULT_ROI_CONFIG_FILE", tmp_path / "roi.json"),
        patch("workflows.tmcs_zdx_fullsite_plan_create.steps.run_ops_json") as mock_ops,
    ):
        mock_ops.return_value = {
            "success": True, "platform": "tmcs", "command": "zdx fullsite-plan create",
            "data": {"executed": False, "created": False, "simulated": True, "dry_run": True,
                     "context_path": str(tmp_path / "ctx.json")},
        }
        _make_tmcs_file(tmp_path / "tmcs.xlsx", product_code="777", barcode="BAR777")
        # 控价单格双值 268/298，成本 146
        _make_jst_file(tmp_path / "jst.xlsx", product_code="BAR777", price="268\n298", cost=146.0)
        _make_roi_config(tmp_path / "roi.json")

        result = _run(runner, ["--item-id", "777", "--daily-budget", "100"], dry_run=True)

    assert _is_success(result), result.errors
    out = {s.step_id: s.outputs for s in result.steps}["resolve_target_roi"]
    assert out["roi_source"] == "tmcs_sku_roi"
    assert out["price"] == 298.0  # 取最高价
    assert out["target_roi"] > 0
