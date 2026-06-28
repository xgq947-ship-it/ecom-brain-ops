"""失败任务重试队列 workflow 的 step handler。

复用 core/retry_queue.py 的成熟实现（list_retries / replay_retry / replay_all / mark_done），
不重写队列或重放逻辑。重放本身仍通过 run.py 子进程执行对应任务，平台动作仍在各任务内部
经 clients/ops_cli_client.py -> Ops-Cli。

dry-run / 安全语义：
- 默认（不带 retry_id / --all / --done）只查看队列，不重放。
- dry-run 强制 execute=False：replay 会以 --dry-run 跑被重放任务，不触发真实平台写入。
- 真实重试必须 --execute 且非 dry-run。
- --done 标记在 dry-run 下跳过（不修改队列状态）。
"""

from __future__ import annotations

import argparse

from core.runtime import parse_workflow_args, StepContext, failure_result, success_result

from core import retry_queue


def _parse_flags(ctx: StepContext) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("retry_id", nargs="?", default=None)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--done", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    namespace = parse_workflow_args(parser, ctx.inputs.get("args") or [])
    namespace.dry_run = ctx.dry_run or namespace.dry_run
    return namespace


def check_inputs(ctx: StepContext):
    flags = _parse_flags(ctx)
    if flags.done:
        mode = "done"
    elif flags.all:
        mode = "replay_all"
    elif flags.retry_id:
        mode = "replay_one"
    else:
        mode = "view"
    effective_execute = flags.execute and not flags.dry_run
    ctx.state["flags"] = flags
    ctx.state["mode"] = mode
    ctx.state["effective_execute"] = effective_execute
    return success_result(
        outputs={"mode": mode, "dry_run": flags.dry_run, "execute": effective_execute, "retry_id": flags.retry_id}
    )


def load_retry_items(ctx: StepContext):
    rows = retry_queue.list_retries()
    ctx.state["rows"] = rows
    return success_result(
        outputs={
            "pending_count": len(rows),
            "items": [{"retry_id": r.get("retry_id"), "task_name": r.get("task_name"), "reason": r.get("reason")} for r in rows],
        }
    )


def preview_retry(ctx: StepContext):
    mode = ctx.state["mode"]
    flags = ctx.state["flags"]
    rows = ctx.state["rows"]
    if mode == "view":
        plan = "仅查看队列"
    elif mode == "done":
        plan = f"将标记完成：{flags.retry_id}"
    elif mode == "replay_all":
        plan = f"将重放全部 {len(rows)} 个（{'真实执行' if ctx.state['effective_execute'] else 'dry-run'}）"
    else:
        plan = f"将重放 {flags.retry_id}（{'真实执行' if ctx.state['effective_execute'] else 'dry-run'}）"
    return success_result(outputs={"plan": plan, "mode": mode})


def execute_retry(ctx: StepContext):
    mode = ctx.state["mode"]
    flags = ctx.state["flags"]
    execute = ctx.state["effective_execute"]

    if mode == "view":
        return success_result(outputs={"skipped": True, "reason": "查看模式，不重放"})

    if mode == "done":
        if not flags.retry_id:
            return failure_result("--done 需要指定 retry_id")
        if flags.dry_run:
            return success_result(outputs={"skipped": True, "reason": "dry-run 不修改队列状态（不 mark_done）"})
        path = retry_queue.mark_done(flags.retry_id)
        ctx.state["done_path"] = str(path)
        return success_result(outputs={"marked_done": str(path)})

    if mode == "replay_all":
        results = retry_queue.replay_all(execute=execute)
        ctx.state["results"] = results
        outputs = {"replayed": len(results), "execute": execute, "results": results}
        failed = _failed_replays(results)
        if failed:
            return failure_result(_format_replay_errors(failed), outputs=outputs)
        return success_result(outputs=outputs)

    # replay_one
    if not flags.retry_id:
        return failure_result("缺少 retry_id")
    result = retry_queue.replay_retry(flags.retry_id, execute=execute)
    ctx.state["results"] = [result]
    outputs = {"returncode": result.get("returncode"), "execute": execute, "result": result}
    failed = _failed_replays([result])
    if failed:
        return failure_result(_format_replay_errors(failed), outputs=outputs)
    return success_result(outputs=outputs)


def _failed_replays(results: list[dict]) -> list[dict]:
    return [item for item in results if int(item.get("returncode") or 0) != 0]


def _format_replay_errors(results: list[dict]) -> list[str]:
    errors: list[str] = []
    for item in results:
        retry_id = item.get("retry_id") or item.get("id") or "unknown"
        returncode = item.get("returncode")
        detail = str(item.get("stderr") or item.get("stdout") or "").strip()
        errors.append(f"重放失败：{retry_id} returncode={returncode}" + (f"；{detail}" if detail else ""))
    return errors


def collect_outputs(ctx: StepContext):
    flags = ctx.state["flags"]
    rows = ctx.state.get("rows", [])
    return success_result(
        outputs={
            "task": "retry_queue",
            "mode": ctx.state["mode"],
            "dry_run": flags.dry_run,
            "execute": ctx.state["effective_execute"],
            "pending_count": len(rows),
            "results": ctx.state.get("results", []),
            "marked_done": ctx.state.get("done_path"),
        }
    )
