"""聚水潭短信验证码提交 workflow 的 step handler。

层次边界：本层只做参数校验、按需触发「聚水潭揽收监控」、调用 Ops-Cli 检测/提交、
收集结果。绝不出现 JST URL / Cookie / Token / Selector / Playwright / CDP，
也绝不把验证码明文写进 outputs / state（落盘的 args 由 runtime 统一脱敏）。

安全要点：
- code 必须 4 位数字，否则 INVALID_CODE。
- 没有 --execute（且非 dry-run）→ EXECUTE_REQUIRED，绝不提交。
- dry-run 只检测，不触发、不填写、不提交。
- 触发次数硬上限 5，每次有 cooldown，检测到弹窗即停。
- outputs 只输出 masked_code。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from core.config_loader import get_path
from core.runtime import StepContext, failure_result, parse_workflow_args, success_result
from core.runtime.registry import discover_workflow

from clients import jst_sms_challenge as challenge
from clients.ops_cli_client import OpsCommandError, run_ops_command

# 兼容旧机制：未传 --challenge-file 但传了 --trigger-with-pickup-watch 时，仍用揽收监控触发。
PICKUP_WATCH_WORKFLOW_ID = "jst_pickup_watch"
# 保留旧常量名，避免外部 import 失效。
TRIGGER_WORKFLOW_ID = PICKUP_WATCH_WORKFLOW_ID
MIN_CODE_LEN = 4
MAX_CODE_LEN = 6
MAX_TRIGGER_ATTEMPTS_LIMIT = 5


def _runs_dir() -> Path:
    return get_path("runtime_dir") / "runs"


def _mask(code: str) -> str:
    return "*" * (len(code) if code else 4)


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--code", default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--challenge-file", default=None)
    parser.add_argument("--trigger-with-pickup-watch", action="store_true")
    parser.add_argument("--max-trigger-attempts", type=int, default=3)
    parser.add_argument("--trigger-cooldown-seconds", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parser.add_argument("--screenshot-dir", default=None)
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


# --- 平台调用包装（便于测试 monkeypatch）---

def _ops_detect(*, dry_run: bool, screenshot_dir: str | None):
    args = ["jst", "auth", "sms", "detect", "--output", "json"]
    if dry_run:
        args.append("--dry-run")
    if screenshot_dir:
        args.extend(["--screenshot-dir", screenshot_dir])
    return run_ops_command(args, interactive_recovery=False)


def _ops_submit(*, code: str, screenshot_dir: str | None):
    args = ["jst", "auth", "sms", "submit", "--code", code, "--execute", "--output", "json"]
    if screenshot_dir:
        args.extend(["--screenshot-dir", screenshot_dir])
    return run_ops_command(args, interactive_recovery=False)


def run_trigger_workflow(workflow_id: str, args: list[str] | None = None) -> str:
    """真实执行「触发 workflow」（原始触发短信验证的 workflow）以重新弹出验证码弹窗。

    第一版直接重跑原 workflow；原 workflow 为只读监控时安全。有副作用的 workflow 后续
    应升级为 step resume / 幂等恢复（见 README）。返回 run 状态。
    """
    from core.runtime import WorkflowRunner

    args = list(args or [])
    wf = discover_workflow(workflow_id)
    runner = WorkflowRunner(_runs_dir())
    run = runner.run(wf, inputs={"dry_run": False, "args": args}, dry_run=False)
    return run.status


# --- steps ---

def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    code = (flags.code or "").strip()
    masked = _mask(code)

    if not (code.isdigit() and MIN_CODE_LEN <= len(code) <= MAX_CODE_LEN):
        return failure_result(
            [f"INVALID_CODE：验证码必须是 {MIN_CODE_LEN}-{MAX_CODE_LEN} 位数字。"],
            outputs={"error_code": "INVALID_CODE", "masked_code": masked},
        )

    if flags.max_trigger_attempts < 1 or flags.max_trigger_attempts > MAX_TRIGGER_ATTEMPTS_LIMIT:
        return failure_result(
            [f"max_trigger_attempts 必须在 1~{MAX_TRIGGER_ATTEMPTS_LIMIT} 之间，禁止无限触发。"],
            outputs={"error_code": "INVALID_MAX_TRIGGER_ATTEMPTS", "masked_code": masked},
        )

    if flags.timeout_seconds <= 0 or flags.trigger_cooldown_seconds < 0:
        return failure_result(
            ["timeout-seconds 必须 > 0，trigger-cooldown-seconds 不能为负。"],
            outputs={"error_code": "INVALID_TIMING", "masked_code": masked},
        )

    if not flags.dry_run and not flags.execute:
        return failure_result(
            ["EXECUTE_REQUIRED：真实提交验证码必须显式传入 --execute。"],
            outputs={"error_code": "EXECUTE_REQUIRED", "masked_code": masked},
        )

    # 真实验证码只放进不落盘的 ctx.state，绝不进 outputs。
    ctx.state["code"] = code
    ctx.state["flags"] = flags
    ctx.state["masked_code"] = masked
    return success_result(
        outputs={
            "dry_run": flags.dry_run,
            "execute": flags.execute,
            "trigger_with_pickup_watch": flags.trigger_with_pickup_watch,
            "challenge_file": flags.challenge_file,
            "max_trigger_attempts": flags.max_trigger_attempts,
            "trigger_cooldown_seconds": flags.trigger_cooldown_seconds,
            "timeout_seconds": flags.timeout_seconds,
            "masked_code": masked,
        }
    )


def find_trigger_workflow(ctx: StepContext):
    """解析「弹窗过期后用哪个 workflow 重新触发」。

    优先级：
    1. --challenge-file：读 challenge，用其中记录的**原始 workflow_id + args** 触发（通用方案）。
    2. --trigger-with-pickup-watch：旧机制，固定用 jst_pickup_watch（兼容存量揽收监控）。
    3. 都没有：不触发，仅就当前弹窗提交。
    """
    flags = ctx.state["flags"]
    ctx.state["trigger_workflow_id"] = None
    ctx.state["trigger_args"] = []
    ctx.state["challenge"] = None

    # 1. challenge 文件（通用）
    if flags.challenge_file:
        challenge_data = challenge.read_challenge_path(flags.challenge_file)
        if challenge_data and challenge_data.get("workflow_id"):
            ctx.state["challenge"] = challenge_data
            workflow_id = str(challenge_data["workflow_id"])
            workflow_args = [str(a) for a in (challenge_data.get("args") or [])]
            try:
                discover_workflow(workflow_id)
            except SystemExit:
                return failure_result(
                    [f"TRIGGER_WORKFLOW_NOT_FOUND：challenge 记录的触发 workflow {workflow_id} 不存在。"],
                    outputs={"error_code": "TRIGGER_WORKFLOW_NOT_FOUND", "trigger_workflow_id": workflow_id},
                )
            ctx.state["trigger_workflow_id"] = workflow_id
            ctx.state["trigger_args"] = workflow_args
            return success_result(
                outputs={
                    "trigger_workflow_id": workflow_id,
                    "trigger_source": "challenge_file",
                    "challenge_id": challenge_data.get("challenge_id"),
                }
            )
        # challenge 文件给了但读不到/无 workflow：不硬失败，若弹窗仍在仍可直接提交。
        if not flags.trigger_with_pickup_watch:
            return success_result(
                outputs={"trigger_workflow_id": None, "reason": "challenge 文件无有效内容，仅就当前弹窗提交"}
            )

    # 2. 旧机制：固定揽收监控
    if flags.trigger_with_pickup_watch:
        try:
            discover_workflow(PICKUP_WATCH_WORKFLOW_ID)
        except SystemExit:
            return failure_result(
                [f"TRIGGER_WORKFLOW_NOT_FOUND：未找到触发 workflow {PICKUP_WATCH_WORKFLOW_ID}（聚水潭揽收监控）。"],
                outputs={"error_code": "TRIGGER_WORKFLOW_NOT_FOUND", "trigger_workflow_id": PICKUP_WATCH_WORKFLOW_ID},
            )
        ctx.state["trigger_workflow_id"] = PICKUP_WATCH_WORKFLOW_ID
        ctx.state["trigger_args"] = []
        return success_result(
            outputs={"trigger_workflow_id": PICKUP_WATCH_WORKFLOW_ID, "trigger_source": "pickup_watch_legacy"}
        )

    # 3. 不触发
    return success_result(outputs={"trigger_workflow_id": None, "reason": "未启用触发"})


def _detect_once(ctx: StepContext) -> tuple[bool, dict]:
    """返回 (sms_required, detect_data)。dry-run 下浏览器未就绪视为软失败。"""
    flags = ctx.state["flags"]
    result = _ops_detect(dry_run=flags.dry_run, screenshot_dir=flags.screenshot_dir)
    data = dict(result.data or {})
    if result.success:
        return bool(data.get("sms_required")), data
    # 平台返回 success=False（如 BROWSER_NOT_RUNNING）。
    if flags.dry_run:
        # dry-run 只预览：浏览器未连上不算 workflow 失败。
        return False, data
    ctx.state["detect_failed"] = data
    return False, data


def detect_sms_dialog(ctx: StepContext):
    flags = ctx.state["flags"]
    sms_required, data = _detect_once(ctx)
    ctx.state["sms_required"] = sms_required
    ctx.state["detect_data"] = data

    # 真实运行下，平台读取失败（如浏览器未启动）直接中断，避免后续盲目触发/提交。
    if not flags.dry_run and data.get("error_code") and not sms_required:
        return failure_result(
            [f"{data.get('error_code')}：{data.get('error') or '验证码弹窗检测失败'}"],
            outputs={
                "error_code": data.get("error_code"),
                "sms_required": False,
                "matched_signals": data.get("matched_signals", []),
            },
        )

    return success_result(
        outputs={
            "sms_required": sms_required,
            "matched_signals": data.get("matched_signals", []),
            "screenshot_path": data.get("screenshot_path"),
            "source": data.get("source"),
        }
    )


def trigger_with_pickup_watch_if_needed(ctx: StepContext):
    """弹窗不在时，用解析出的「原始触发 workflow」重新触发（不再写死 jst_pickup_watch）。"""
    flags = ctx.state["flags"]
    ctx.state.setdefault("trigger_attempts", 0)
    trigger_workflow_id = ctx.state.get("trigger_workflow_id")
    trigger_args = ctx.state.get("trigger_args") or []

    if ctx.state.get("sms_required"):
        return success_result(outputs={"triggered": False, "reason": "已检测到弹窗，无需触发", "trigger_attempts": 0})
    if not trigger_workflow_id:
        return success_result(outputs={"triggered": False, "reason": "未启用触发", "trigger_attempts": 0})
    if flags.dry_run:
        # dry-run 绝不真实触发平台监控。
        return success_result(outputs={"triggered": False, "reason": "dry-run 跳过触发", "skipped": True, "trigger_attempts": 0})

    attempts = 0
    for attempts in range(1, flags.max_trigger_attempts + 1):
        try:
            run_trigger_workflow(trigger_workflow_id, trigger_args)
        except Exception as exc:  # noqa: BLE001 - 触发失败记录后停止，不无限重试
            ctx.state["trigger_attempts"] = attempts
            return failure_result(
                [f"触发 workflow 执行失败：{exc}"],
                outputs={"error_code": "TRIGGER_WORKFLOW_FAILED", "trigger_attempts": attempts},
            )
        if flags.trigger_cooldown_seconds > 0:
            time.sleep(flags.trigger_cooldown_seconds)
        sms_required, data = _detect_once(ctx)
        if sms_required:
            ctx.state["sms_required"] = True
            ctx.state["detect_data"] = data
            ctx.state["trigger_attempts"] = attempts
            return success_result(
                outputs={
                    "triggered": True,
                    "trigger_attempts": attempts,
                    "sms_required": True,
                    "trigger_workflow_id": trigger_workflow_id,
                }
            )

    ctx.state["trigger_attempts"] = attempts
    return failure_result(
        [f"TRIGGER_ATTEMPTS_EXCEEDED：用 {trigger_workflow_id} 触发 {attempts} 次后仍未出现短信验证码弹窗。"],
        outputs={"error_code": "TRIGGER_ATTEMPTS_EXCEEDED", "trigger_attempts": attempts, "trigger_workflow_id": trigger_workflow_id},
    )


def submit_sms_code(ctx: StepContext):
    flags = ctx.state["flags"]
    masked = ctx.state["masked_code"]

    if flags.dry_run:
        return success_result(
            outputs={
                "submitted": False,
                "skipped": True,
                "reason": "dry-run 跳过：只检测，不填写、不提交",
                "sms_required": ctx.state.get("sms_required", False),
                "masked_code": masked,
            }
        )

    if not ctx.state.get("sms_required"):
        if ctx.state.get("trigger_workflow_id"):
            code, hint = "TRIGGER_ATTEMPTS_EXCEEDED", "多次触发后仍无验证码弹窗"
        else:
            code, hint = "SMS_DIALOG_NOT_FOUND", "未检测到短信验证码弹窗"
        return failure_result(
            [f"{code}：{hint}。"],
            outputs={"error_code": code, "submitted": False, "masked_code": masked},
        )

    if not flags.execute:
        return failure_result(
            ["EXECUTE_REQUIRED：未传入 --execute，绝不提交验证码。"],
            outputs={"error_code": "EXECUTE_REQUIRED", "submitted": False, "masked_code": masked},
        )

    try:
        result = _ops_submit(code=ctx.state["code"], screenshot_dir=flags.screenshot_dir)
    except OpsCommandError as exc:
        return failure_result(
            [f"验证码提交失败：{exc}"],
            outputs={"error_code": exc.result.error_code or "SMS_SUBMIT_FAILED", "submitted": False, "masked_code": masked},
        )

    data = dict(result.data or {})
    if not result.success:
        return failure_result(
            [f"{data.get('error_code') or 'SMS_SUBMIT_FAILED'}：{data.get('error') or '验证码提交失败'}"],
            outputs={
                "error_code": data.get("error_code") or "SMS_SUBMIT_FAILED",
                "submitted": bool(data.get("submitted")),
                "verified": bool(data.get("verified")),
                "masked_code": masked,
            },
        )

    ctx.state["submitted"] = bool(data.get("submitted"))
    ctx.state["verified"] = bool(data.get("verified"))

    # 验证通过且走的是 challenge-file：把 challenge 标记 verified，供 Hermes 据此执行 resume_command。
    if ctx.state["verified"] and flags.challenge_file:
        try:
            challenge.update_challenge_path(flags.challenge_file, challenge.STATUS_VERIFIED)
        except Exception:  # noqa: BLE001 - 标记失败不影响验证本身
            pass

    return success_result(
        outputs={
            "submitted": bool(data.get("submitted")),
            "verified": bool(data.get("verified")),
            "masked_code": data.get("masked_code", masked),
            "source": data.get("source"),
        }
    )


def verify_session_restored(ctx: StepContext):
    """确认验证是否通过。

    Ops-Cli 的 submit 能力在提交后已内部复检弹窗是否消失并返回 verified，
    这里直接采信，不再额外发起平台检测（避免重复连 9222）。
    """
    flags = ctx.state["flags"]
    masked = ctx.state["masked_code"]
    verified = bool(ctx.state.get("verified", False))
    return success_result(
        outputs={
            "verified": verified,
            "skipped": flags.dry_run or not ctx.state.get("submitted"),
            "masked_code": masked,
        }
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    challenge_data = ctx.state.get("challenge") or {}
    return success_result(
        outputs={
            "task": "jst_sms_verification_submit",
            "dry_run": flags.dry_run,
            "submitted": ctx.state.get("submitted", False),
            "verified": ctx.state.get("verified", False),
            "sms_required_before": ctx.state.get("sms_required", False),
            "trigger_workflow_id": ctx.state.get("trigger_workflow_id"),
            "trigger_attempts": ctx.state.get("trigger_attempts", 0),
            "challenge_id": challenge_data.get("challenge_id"),
            "resume_command": challenge_data.get("resume_command"),
            "masked_code": ctx.state["masked_code"],
        }
    )
