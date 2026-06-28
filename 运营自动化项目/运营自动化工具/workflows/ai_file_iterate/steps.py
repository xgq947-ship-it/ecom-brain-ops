"""ai_file_iterate workflow 的 step handler。

工具类 workflow：纯本地编排 claude / codex CLI，不写平台逻辑、不调 Ops-Cli。

dry-run 安全点（CLAUDE.md 第 7 节）：
- dry-run 下绝不真实调用付费 AI、绝不写文件；改为对两个 AI 做体检（--check-agents 级），
  并返回 skipped 输出，不产出成品。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from core.config_loader import PROJECT_ROOT
from core.notchflow_reporter import notchflow
from core.runtime import Artifact, StepContext, failure_result, parse_workflow_args, success_result

from workflows.ai_file_iterate import engine

RUN_ROOT = PROJECT_ROOT / "runtime" / "ai_iterate"

# 每轮 outcome → NotchFlow 文案
_OUTCOME_CN = {"edited": "已修改", "converged": "已收敛", "failed": "本轮失败"}


def _make_progress_reporter(ctx: StepContext):
    """把 engine 每轮进度 event 转成 NotchFlow 逐轮上报。

    run.py 只在 workflow 首尾推 start/success；这里补上中间「第 X/N 轮 · agent 打磨中」，
    让 NotchFlow 在长时间迭代期间也能显示实时进度。App 没开时 notchflow 自身静默 no-op。
    """
    wid = ctx.run.workflow_id
    name = ctx.run.workflow_name

    def report(event: dict) -> None:
        total = max(1, int(event.get("max_rounds") or 1))
        rnd = int(event.get("round") or 0)
        agent = event.get("agent") or "AI"
        if event.get("phase") == "round_start":
            notchflow.step(wid, name, f"第 {rnd}/{total} 轮 · {agent} 打磨中…",
                           progress=(rnd - 1) / total, dry_run=ctx.dry_run)
        else:
            outcome = _OUTCOME_CN.get(event.get("outcome"), event.get("outcome") or "")
            notchflow.step(wid, name, f"第 {rnd}/{total} 轮 · {agent} {outcome}",
                           progress=rnd / total, dry_run=ctx.dry_run)

    return report


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--request", default=None, help="一句话请求，自动抽路径+标准")
    parser.add_argument("--target", default=None, help="要优化的文件")
    parser.add_argument("--goal", default=None, help="优化标准（文件路径或直接文字）")
    parser.add_argument("--max-rounds", type=int, default=None)
    parser.add_argument("--check-agents", action="store_true")
    ns = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    ns.dry_run = ctx.dry_run or ns.dry_run
    return ns


def _resolve_brief(goal: str | None) -> str:
    if not goal:
        return ""
    p = Path(goal).expanduser()
    if p.is_file():
        return engine.read_text(p)
    return goal  # 当作内联文字


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    cfg = engine.load_engine_config(PROJECT_ROOT,
                                    overrides={"max_rounds": flags.max_rounds})
    ctx.state["cfg"] = cfg
    ctx.state["flags"] = flags

    if flags.check_agents:
        return success_result(outputs={"mode": "check_agents", "dry_run": flags.dry_run})

    target = None
    brief = ""
    if flags.request:
        target, brief = engine.parse_request(flags.request)
        if not target and not flags.dry_run:
            return failure_result(errors=[f"无法从请求里找到存在的文件路径：{flags.request!r}"])
    elif flags.target:
        target = str(Path(flags.target).expanduser())
        if not Path(target).is_file() and not flags.dry_run:
            return failure_result(errors=[f"目标文件不存在：{target}"])
    elif not flags.dry_run:
        return failure_result(errors=["缺少 --request 或 --target：请指定要优化的文件"])

    if flags.goal:
        brief = _resolve_brief(flags.goal)

    ctx.state["target"] = target
    ctx.state["brief"] = brief
    return success_result(outputs={
        "target_file": target,
        "has_brief": bool(brief.strip()),
        "max_rounds": cfg["max_rounds"],
        "order": cfg["order"],
        "dry_run": flags.dry_run,
    })


def iterate(ctx: StepContext):
    flags = ctx.state["flags"]
    cfg = ctx.state["cfg"]
    RUN_ROOT.mkdir(parents=True, exist_ok=True)

    # 体检模式：只验证能否调用，不跑迭代
    if flags.check_agents:
        report = engine.check_agents(cfg, RUN_ROOT)
        all_ok = all(v["ok"] for v in report.values())
        return success_result(outputs={"check_agents": report, "all_ok": all_ok})

    # dry-run：绝不真实调用 AI、不写文件，只做离线校验（命令是否可找到 + 目标是否就绪）
    if flags.dry_run:
        target = ctx.state.get("target")
        return success_result(outputs={
            "skipped": True,
            "reason": "dry-run 跳过真实 AI 调用与文件写入",
            "target_ready": bool(target) and Path(target).is_file(),
            "agents_resolvable": engine.agents_resolvable(cfg),
            "tip": "真正验证 AI 能否调用请用 --check-agents（会实际发一句话）",
        })

    target = ctx.state["target"]
    brief = ctx.state["brief"]
    status = engine.run_iteration(cfg, Path(target), brief, RUN_ROOT,
                                  max_rounds=cfg["max_rounds"],
                                  on_progress=_make_progress_reporter(ctx))
    ctx.state["status"] = status

    final_path = status["final_file"]
    suffix = Path(final_path).suffix.lstrip(".") or "txt"
    artifact = Artifact(
        type=suffix, role="output", name=Path(final_path).name, path=final_path,
        metadata={
            "rounds_run": status["rounds_run"],
            "stop_reason": status["stop_reason"],
            "result": status["result"],
            "rounds": [{"round": r["round"], "agent": r["agent"],
                        "outcome": r["outcome"], "similarity": r["similarity"]}
                       for r in status["rounds"]],
        },
    )
    if status["result"] != "completed":
        # 仍保留成品（已回滚到最近成功态，非半成品），但用 failure 让 TaskRun 标记 failed
        return failure_result(
            errors=[f"迭代未正常完成：stop_reason={status['stop_reason']}"],
            outputs={"final_file": final_path, "stop_reason": status["stop_reason"],
                     "rounds_run": status["rounds_run"], "warnings": status.get("warnings") or []},
            artifacts=[artifact],
        )
    return success_result(
        outputs={"final_file": final_path, "stop_reason": status["stop_reason"],
                 "rounds_run": status["rounds_run"], "archive_dir": status.get("archive_dir"),
                 "warnings": status.get("warnings") or []},
        artifacts=[artifact],
    )


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    if flags.check_agents:
        return success_result(outputs={"task": "ai_file_iterate", "mode": "check_agents"})
    if flags.dry_run:
        return success_result(outputs={"task": "ai_file_iterate", "dry_run": True, "skipped": True})
    status = ctx.state.get("status") or {}
    return success_result(outputs={
        "task": "ai_file_iterate",
        "final_file": status.get("final_file"),
        "rounds_run": status.get("rounds_run"),
        "stop_reason": status.get("stop_reason"),
        "result": status.get("result"),
        "archive_dir": status.get("archive_dir"),
        "agent_stats": status.get("agent_stats") or {},
        "warnings": status.get("warnings") or [],
    })
