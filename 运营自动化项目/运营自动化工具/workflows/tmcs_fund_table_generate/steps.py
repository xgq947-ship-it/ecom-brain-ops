from __future__ import annotations

import argparse
import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

from clients.ops_cli_client import run_ops_json
from core.config_loader import get_path
from core.runtime import parse_workflow_args, Artifact, StepContext, failure_result, success_result

from workflows.tmcs_fund_table_generate.excel_generator import generate_fund_table, verify_fund_table


def _previous_month(today: date | None = None) -> str:
    today = today or date.today()
    year = today.year
    month = today.month - 1
    if month == 0:
        year -= 1
        month = 12
    return f"{year:04d}-{month:02d}"


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--month", default=ctx.inputs.get("month") or _previous_month())
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--screenshot-dir", default=None)
    parser.add_argument("--output-file", default=None)
    parser.add_argument("--reserve-balance", default=0)
    parser.add_argument("--bank-card-balance", default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    output_dir = Path(flags.output_dir).expanduser() if flags.output_dir else get_path("desktop_dir")
    if not output_dir.is_dir():
        return failure_result(f"输出目录不存在：{output_dir}")
    try:
        reserve_balance = _number(flags.reserve_balance, "备用金余额")
        bank_card_balance = _number(flags.bank_card_balance, "银行卡余额")
    except ValueError as exc:
        return failure_result(str(exc))
    stamp = _stamp()
    output_file = Path(flags.output_file).expanduser() if flags.output_file else output_dir / f"猫超资金表_{flags.month}_{stamp}.xlsx"
    # 截图只作为临时凭证嵌入 Excel，不在桌面建文件夹：默认落临时目录，嵌入后清理。
    if flags.screenshot_dir:
        screenshot_dir = Path(flags.screenshot_dir).expanduser()
        screenshot_dir_is_temp = False
    else:
        screenshot_dir = Path(tempfile.mkdtemp(prefix=f"猫超资金表_{flags.month}_截图_"))
        screenshot_dir_is_temp = True
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    ctx.state["flags"] = flags
    ctx.state["month"] = flags.month
    ctx.state["reserve_balance"] = reserve_balance
    ctx.state["bank_card_balance"] = bank_card_balance
    ctx.state["output_file"] = output_file
    ctx.state["screenshot_dir"] = screenshot_dir
    ctx.state["screenshot_dir_is_temp"] = screenshot_dir_is_temp
    return success_result(
        outputs={
            "month": flags.month,
            "dry_run": flags.dry_run,
            "reserve_balance": reserve_balance,
            "bank_card_balance": bank_card_balance,
            "output_file": str(output_file),
        }
    )


def fetch_receivable_amount(ctx: StepContext):
    flags = ctx.state["flags"]
    command = [
        "--json",
        "tmcs",
        "fund",
        "receivable-bill",
        "sum",
        "--month",
        ctx.state["month"],
        "--screenshot-dir",
        str(ctx.state["screenshot_dir"]),
    ]
    if flags.dry_run:
        command.append("--dry-run")
    try:
        payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        return failure_result(f"Ops-Cli 调用失败：{exc}")
    data = payload.get("data") or {}
    ctx.state["receivable_data"] = data
    return success_result(
        outputs={
            "receivable_amount": data.get("total_amount"),
            "receivable_screenshot": data.get("screenshot_path"),
            "receivable_source": data.get("source"),
            "receivable_simulated": bool(data.get("simulated", False)),
        }
    )


def fetch_promotion_balance(ctx: StepContext):
    flags = ctx.state["flags"]
    command = [
        "--json",
        "tmcs",
        "fund",
        "promotion-balance",
        "sum",
        "--screenshot-dir",
        str(ctx.state["screenshot_dir"]),
    ]
    if flags.dry_run:
        command.append("--dry-run")
    try:
        payload = run_ops_json(command, interactive_recovery=not flags.dry_run)
    except RuntimeError as exc:
        return failure_result(f"Ops-Cli 调用失败：{exc}")
    data = payload.get("data") or {}
    ctx.state["promotion_data"] = data
    return success_result(
        outputs={
            "promotion_balance": data.get("total_amount"),
            "promotion_screenshot": data.get("screenshot_path"),
            "promotion_source": data.get("source"),
            "promotion_simulated": bool(data.get("simulated", False)),
        }
    )


def _number(value: Any, label: str) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 不是数字：{value}") from exc
    if amount < 0:
        raise ValueError(f"{label} 金额不能为负：{amount}")
    return round(amount, 2)


def validate_amounts(ctx: StepContext):
    receivable = ctx.state.get("receivable_data") or {}
    promotion = ctx.state.get("promotion_data") or {}
    try:
        receivable_amount = _number(receivable.get("total_amount"), "待收货款")
        promotion_balance = _number(promotion.get("total_amount"), "推广账户余额")
    except ValueError as exc:
        return failure_result(str(exc))
    screenshots = [receivable.get("screenshot_path"), promotion.get("screenshot_path")]
    missing = [str(path) for path in screenshots if not path or not Path(str(path)).expanduser().is_file()]
    if missing:
        return failure_result([f"截图不存在：{path}" for path in missing])
    ctx.state["receivable_amount"] = receivable_amount
    ctx.state["promotion_balance"] = promotion_balance
    return success_result(outputs={"receivable_amount": receivable_amount, "promotion_balance": promotion_balance})


def generate_fund_table_step(ctx: StepContext):
    receivable = ctx.state["receivable_data"]
    promotion = ctx.state["promotion_data"]
    try:
        result = generate_fund_table(
            output_file=ctx.state["output_file"],
            month=ctx.state["month"],
            receivable_amount=ctx.state["receivable_amount"],
            promotion_balance=ctx.state["promotion_balance"],
            receivable_screenshot=receivable["screenshot_path"],
            promotion_screenshot=promotion["screenshot_path"],
            reserve_balance=ctx.state["reserve_balance"],
            bank_card_balance=ctx.state["bank_card_balance"],
        )
    except Exception as exc:
        return failure_result(f"EXCEL_GENERATE_FAILED：{exc}")
    ctx.state["formula_check_result"] = result.formula_check_result
    artifact = Artifact(
        type="xlsx",
        role="output",
        name=result.output_file.name,
        path=str(result.output_file),
        platform="tmcs",
        month=ctx.state["month"],
        metadata={
            "receivable_amount": ctx.state["receivable_amount"],
            "promotion_balance": ctx.state["promotion_balance"],
            "reserve_balance": ctx.state["reserve_balance"],
            "bank_card_balance": ctx.state["bank_card_balance"],
            "screenshots_embedded": True,
        },
    )
    return success_result(
        outputs={
            "output_file": str(result.output_file),
            "formula_check_result": result.formula_check_result,
        },
        artifacts=[artifact],
    )


def verify_generated_excel(ctx: StepContext):
    output_file = ctx.state["output_file"]
    if not output_file.is_file():
        return failure_result(f"EXCEL_GENERATE_FAILED：输出文件不存在：{output_file}")
    formula_check_result = verify_fund_table(output_file)
    if not all(formula_check_result.values()):
        return failure_result("FORMULA_MISSING：Q2 或 S2 不是公式", outputs={"formula_check_result": formula_check_result})
    ctx.state["formula_check_result"] = formula_check_result
    return success_result(outputs={"formula_check_result": formula_check_result})


def collect_outputs(ctx: StepContext):
    # 截图已嵌入 Excel，清理临时凭证目录，桌面不留图片文件夹。
    if ctx.state.get("screenshot_dir_is_temp"):
        shutil.rmtree(ctx.state["screenshot_dir"], ignore_errors=True)
    return success_result(
        outputs={
            "month": ctx.state["month"],
            "receivable_amount": ctx.state["receivable_amount"],
            "promotion_balance": ctx.state["promotion_balance"],
            "reserve_balance": ctx.state["reserve_balance"],
            "bank_card_balance": ctx.state["bank_card_balance"],
            "output_file": str(ctx.state["output_file"]),
            "screenshots_embedded": True,
            "formula_check_result": ctx.state.get("formula_check_result"),
        }
    )
