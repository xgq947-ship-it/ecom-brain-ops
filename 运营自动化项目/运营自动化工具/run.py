#!/usr/bin/env python3
"""Unified entry point for local operations automation."""

from __future__ import annotations

import argparse
import json
import importlib.util
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ── 业务层解释器自举 ───────────────────────────────────────────────────────────
# 业务层固定运行在自带的 运营自动化工具/.venv（Python 3.11）里。无论用系统 python3、
# Ops-Cli 的 venv，还是别的解释器启动 run.py，这里都会在执行任何业务逻辑前 re-exec 到
# 业务 venv，使「手动执行」与「Hermes 定时执行」收敛到同一解释器与依赖集（openpyxl /
# Pillow / requests），避免 3.9 vs 3.11 的行为或依赖分叉。
# venv 不存在时静默回退当前解释器；仅作为脚本入口时触发，被测试 import 时不 re-exec。
def _maybe_reexec_in_business_venv() -> None:
    if os.environ.get("OPS_BUSINESS_VENV_BOOTSTRAPPED") == "1":
        return
    venv_dir = Path(__file__).resolve().parent / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists():
        return
    # 用 sys.prefix 判断「是否已在业务 venv 内」，而不是比较可执行文件路径：
    # 业务 venv 与 Ops-Cli venv 可能基于同一个 uv 底层 3.11，resolve() 后会指向同一
    # cpython，导致从 Ops-Cli venv 启动时被误判为「已在业务 venv」而不切换。
    try:
        if Path(sys.prefix).resolve() == venv_dir.resolve():
            return
    except OSError:
        return
    os.environ["OPS_BUSINESS_VENV_BOOTSTRAPPED"] = "1"
    os.execv(str(venv_python), [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    _maybe_reexec_in_business_venv()

from core.config_loader import get_path
from core.notchflow_reporter import notchflow
from core.task_context import TaskContext
from core.task_registry import resolve_task, task_required_modules, task_scripts


ROOT = Path(__file__).resolve().parent
LOG_DIR = get_path("logs_dir")
RUNS_DIR = get_path("runtime_dir") / "runs"

TASKS = task_scripts()

# 验证码提交 workflow 本身允许在有 pending challenge 时运行（它就是来解决 challenge 的）。
SMS_SUBMIT_WORKFLOW_ID = "jst_sms_verification_submit"


class _NoopNotchFlowReporter:
    def start(self, *args, **kwargs) -> None:
        return None

    def step(self, *args, **kwargs) -> None:
        return None

    def success(self, *args, **kwargs) -> None:
        return None

    def failed(self, *args, **kwargs) -> None:
        return None

    def waiting(self, *args, **kwargs) -> None:
        return None


_NOOP_NOTCHFLOW = _NoopNotchFlowReporter()


def _notchflow_reporter():
    if os.environ.get("NF_DISABLE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return _NOOP_NOTCHFLOW
    return notchflow


def _detect_phone_mask() -> str | None:
    """尽力读取当前 9222 弹窗里的脱敏手机号，用于 challenge / 飞书文案。失败返回 None。"""
    try:
        from clients.ops_cli_client import run_ops_command

        result = run_ops_command(["jst", "auth", "sms", "detect", "--output", "json"], interactive_recovery=False)
        if result.success:
            value = result.data.get("phone_mask")
            return str(value) if value else None
    except Exception:
        return None
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运营自动化工具统一入口")
    parser.add_argument("--list", action="store_true", help="列出当前已注册任务，不执行业务")
    parser.add_argument("task", nargs="?", help="要执行的任务，支持相近说法")
    parser.add_argument("task_args", nargs=argparse.REMAINDER, help="传给任务脚本的参数")
    args = parser.parse_args()
    if args.list:
        return args
    if not args.task:
        parser.error("缺少任务名；可用 --list 查看已注册任务")
    first_flag_index = next((index for index, part in enumerate(args.task_args) if part.startswith("-")), len(args.task_args))
    natural_parts = args.task_args[:first_flag_index]
    option_parts = args.task_args[first_flag_index:]
    raw_text = " ".join([args.task, *natural_parts]).strip()
    resolved_task = resolve_task(args.task)
    if resolved_task != "company_nas_listing" and raw_text != args.task:
        try:
            resolved_task = resolve_task(raw_text)
        except SystemExit:
            pass
    args.task = resolved_task
    if args.task == "company_nas_listing" and raw_text != "company_nas_listing" and "--text" not in option_parts:
        args.task_args = ["--text", raw_text, *option_parts]
    return args


def python_has_modules(python_path: Path, modules: tuple[str, ...]) -> bool:
    if not python_path.exists():
        return False
    command = [
        str(python_path),
        "-c",
        "import importlib.util, sys; "
        "mods=sys.argv[1:]; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "raise SystemExit(1 if missing else 0)",
        *modules,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    return result.returncode == 0


def python_candidates() -> list[Path]:
    candidates = [
        Path(sys.executable),
        Path("/usr/bin/python3"),
        Path("/usr/local/bin/python3"),
        Path("/opt/homebrew/bin/python3"),
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def choose_python(task_name: str) -> str:
    required_modules = task_required_modules().get(task_name, ())
    for candidate in python_candidates():
        if python_has_modules(candidate, required_modules):
            return str(candidate)
    return sys.executable


def write_log(task: str, payload: dict) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = LOG_DIR / f"{task}_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def run_workflow(workflow_args: list[str]) -> int:
    """workflow 子命令入口：python3 run.py workflow <id> [--dry-run] [...]。

    完全绕开旧任务解析（resolve_task）。无论成功失败都写外层 TaskContext + 日志；
    workflow 实际执行的 step 级记录由 WorkflowRunner 落到 runtime/runs/。
    """
    from core.runtime import WorkflowRunner
    from core.runtime.registry import available_workflows, discover_workflow

    if not workflow_args:
        valid = "、".join(available_workflows()) or "（无）"
        print(f"缺少 workflow id；用法：python3 run.py workflow <id> [--dry-run]\n可用 workflow：{valid}", file=sys.stderr)
        return 2

    workflow_id = workflow_args[0]
    extra_args = workflow_args[1:]
    dry_run = "--dry-run" in extra_args
    month = None
    if "--month" in extra_args:
        index = extra_args.index("--month")
        if index + 1 < len(extra_args):
            month = extra_args[index + 1]

    from core.runtime.secrets import redact_cli_args

    context = TaskContext(f"workflow_{workflow_id}")
    context.add_input("workflow_id", workflow_id)
    # 任务上下文落盘前对敏感参数（--code 等）脱敏，避免明文进 runtime/context。
    context.add_input("workflow_args", redact_cli_args(extra_args))
    context.add_input("dry_run", dry_run)
    if month is not None:
        context.add_input("month", month)

    nf = _notchflow_reporter()
    inputs: dict = {"dry_run": dry_run, "args": extra_args}
    if month is not None:
        inputs["month"] = month

    try:
        workflow = discover_workflow(workflow_id)
    except SystemExit as exc:
        message = str(exc)
        context.add_error(message)
        context_path = context.finish("failed")
        print(message, file=sys.stderr)
        print(f"任务上下文：{context_path}")
        return 2
    task_name = workflow.name or workflow_id

    # 单并发闸门：已有待处理短信验证 challenge 时，其他 JST 任务不再请求聚水潭，避免叠加触发。
    from clients import jst_sms_challenge as sms_challenge

    if not dry_run and sms_challenge.is_jst_workflow(workflow_id) and workflow_id != SMS_SUBMIT_WORKFLOW_ID:
        active = sms_challenge.active_challenge()
        if active is not None:
            message = (
                "WAITING_SMS_VERIFICATION：已有待处理的聚水潭短信验证 challenge "
                f"（{active.get('workflow_name')}），本次 {workflow_id} 暂不请求聚水潭，等待验证完成。"
            )
            print(message, file=sys.stderr)
            print("::JST_SMS_WAITING::" + json.dumps(active, ensure_ascii=False))
            context.add_output("status", "waiting_sms_verification")
            context.add_output("active_challenge", active)
            context_path = context.finish("partial")
            nf.waiting(workflow_id, task_name, "等待短信验证完成", dry_run=dry_run)
            print(f"任务上下文：{context_path}")
            return 0

    started_at = datetime.now().isoformat(timespec="seconds")
    runner = WorkflowRunner(RUNS_DIR)
    nf.start(workflow_id, task_name, dry_run=dry_run)
    try:
        run = runner.run(workflow, inputs=inputs, dry_run=dry_run)
    except Exception as exc:
        nf.failed(workflow_id, task_name, str(exc), dry_run=dry_run)
        raise
    finished_at = datetime.now().isoformat(timespec="seconds")

    run_dict = run.to_dict()

    # 通用短信验证中断：任意 JST workflow 因短信验证失败时，自动登记 challenge（记录原
    # workflow_id + args，供弹窗过期后用原 workflow 重新触发），并打印结构化 marker。
    # 飞书发送不在这里——由 Hermes / shell wrapper 读取 challenge 后发送，保持 workflow 不接飞书。
    challenge_block = None
    if (
        not dry_run
        and run.status not in {"success", "dry_run_success"}
        and sms_challenge.is_jst_workflow(workflow_id)
        and workflow_id != SMS_SUBMIT_WORKFLOW_ID
        and sms_challenge.errors_indicate_sms(run.errors)
    ):
        challenge_block, _created = sms_challenge.create_challenge(
            workflow_id=workflow_id,
            workflow_name=workflow.name,
            args=extra_args,
            phone_mask=_detect_phone_mask(),
            context_path=str(runner.last_run_dir) if runner.last_run_dir else None,
        )
        run_dict["status"] = "waiting_sms_verification"
        run_dict["error_code"] = "AUTH_SMS_REQUIRED"
        run_dict["challenge"] = challenge_block
        context.add_output("challenge", challenge_block)
        print("::JST_SMS_CHALLENGE::" + json.dumps(challenge_block, ensure_ascii=False))

    log_payload = {
        "workflow_id": workflow_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "dry_run": dry_run,
        "run": run_dict,
    }
    log_path = write_log(f"workflow_{workflow_id}", log_payload)

    context.add_output("run_id", run.run_id)
    context.add_output("status", run.status)
    context.add_output("started_at", started_at)
    context.add_output("finished_at", finished_at)
    context.add_output("log_path", log_path)
    context.add_artifact(log_path, kind="run_log")
    if runner.last_run_dir is not None:
        context.add_output("run_dir", str(runner.last_run_dir))
        context.add_artifact(runner.last_run_dir / "run.json", kind="workflow_run")
    for artifact in run.artifacts:
        if artifact.path:
            context.add_artifact(artifact.path, kind=artifact.role or artifact.type)

    succeeded = run.status in {"success", "dry_run_success"}
    if not succeeded:
        for err in run.errors:
            context.add_error(err)
        context_status = "failed"
    elif dry_run:
        context_status = "dry_run_success"
    else:
        context_status = "success"
    context_path = context.finish(context_status)

    print(json.dumps(run_dict, ensure_ascii=False, indent=2))
    print(f"\n日志：{log_path}")
    if runner.last_run_dir is not None:
        print(f"运行目录：{runner.last_run_dir}")
    print(f"任务上下文：{context_path}")
    if challenge_block is not None:
        nf.waiting(workflow_id, task_name, "等待短信验证输入", dry_run=dry_run)
    elif succeeded:
        nf.success(workflow_id, task_name, dry_run=dry_run)
    else:
        nf.failed(workflow_id, task_name, run.errors[0] if run.errors else "执行失败", dry_run=dry_run)
    return 0 if succeeded else 1


def list_runs_cli(extra_args: list[str]) -> int:
    """python3 run.py runs [--limit N] [--workflow ID] [--reindex]。"""
    from core.runtime import RunIndex

    parser = argparse.ArgumentParser(prog="run.py runs", description="列出 workflow 历史运行")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--workflow", default=None)
    parser.add_argument("--reindex", action="store_true", help="从现有 run.json 重建索引")
    args = parser.parse_args(extra_args)

    index = RunIndex(RUNS_DIR)
    if args.reindex:
        count = index.reindex()
        print(f"已重建索引：{count} 条 -> {index.index_path}")
    runs = index.list_runs(limit=args.limit, workflow_id=args.workflow)
    print(json.dumps(runs, ensure_ascii=False, indent=2))
    return 0


def search_artifacts_cli(extra_args: list[str]) -> int:
    """python3 run.py artifacts [query] [--role R] [--platform P] [--month M] [--limit N]。"""
    from core.runtime import RunIndex

    parser = argparse.ArgumentParser(prog="run.py artifacts", description="检索 workflow 产物")
    parser.add_argument("query", nargs="?", default=None)
    parser.add_argument("--role", default=None)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--month", default=None)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(extra_args)

    results = RunIndex(RUNS_DIR).search_artifacts(
        args.query, role=args.role, platform=args.platform, month=args.month, limit=args.limit
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def sms_challenge_cli(extra_args: list[str]) -> int:
    """python3 run.py sms-challenge status|cancel|clear|create。

    供 Hermes / shell wrapper 读写聚水潭短信验证 challenge 状态（不发飞书、不连平台）。
    """
    from clients import jst_sms_challenge as sms_challenge

    action = extra_args[0] if extra_args else None
    rest = extra_args[1:]

    if action == "status":
        active = sms_challenge.active_challenge()
        payload = {"active": active is not None, "challenge": active or sms_challenge.read_challenge()}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if action == "cancel":
        cancelled = sms_challenge.cancel_challenge()
        print(json.dumps({"cancelled": cancelled is not None, "challenge": cancelled}, ensure_ascii=False, indent=2))
        return 0
    if action == "clear":
        sms_challenge.clear_challenge()
        print(json.dumps({"cleared": True}, ensure_ascii=False))
        return 0
    if action == "create":
        # 已知 flag 之外的所有参数都当作「原 workflow 的参数」，从而支持 --days 7 --execute 等。
        parser = argparse.ArgumentParser(prog="run.py sms-challenge create")
        parser.add_argument("--workflow", required=True)
        parser.add_argument("--name", default="")
        parser.add_argument("--phone-mask", default=None)
        parser.add_argument("--ttl-seconds", type=int, default=sms_challenge.DEFAULT_TTL_SECONDS)
        known, wf_args = parser.parse_known_args(rest)
        wf_args = [a for a in wf_args if a != "--"]
        challenge, created = sms_challenge.create_challenge(
            workflow_id=known.workflow,
            workflow_name=known.name,
            args=wf_args,
            phone_mask=known.phone_mask,
            ttl_seconds=known.ttl_seconds,
        )
        print(json.dumps({"created": created, "challenge": challenge}, ensure_ascii=False, indent=2))
        return 0

    print("用法：run.py sms-challenge status|cancel|clear|create [--workflow ... -- <原参数>]", file=sys.stderr)
    return 2


def setup_cli(extra_args: list[str]) -> int:
    """python3 run.py setup [--force]。

    首次/换机配置向导：体检所有路径是否能解析、自动探测无法推导的路径（微信目录、
    NAS 挂载），并把探测到的值写入 config/paths.local.yaml（gitignore，本机覆盖）。
    绝大多数路径已从仓库位置 / Path.home() 自动推导，无需配置。
    """
    from core import config_loader as cfg

    parser = argparse.ArgumentParser(prog="run.py setup")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 paths.local.yaml")
    args = parser.parse_args(extra_args)

    paths = dict(cfg.load_paths())
    # 微信目录在 load_paths 里是惰性的（避免每次解析触碰 TCC 容器）；setup 是交互场景，
    # 这里显式补一次用于体检显示。
    if "wechat_file_dir" not in paths:
        wechat_for_display = cfg._cached_wechat_file_dir()
        if wechat_for_display is not None:
            paths["wechat_file_dir"] = wechat_for_display
    print("== 路径体检（已解析） ==")
    missing_required: list[str] = []
    for key in sorted(cfg._KNOWN_KEYS):
        value = paths.get(key)
        if value is None:
            mark = "✗ 未解析"
            if key in cfg._REQUIRES_LOCAL_CONFIG:
                missing_required.append(key)
        else:
            exists = "*" not in value.name and value.exists()
            mark = "✓" if exists else "·"  # · = 路径已确定但文件/目录尚不存在（正常）
        print(f"  [{mark}] {key:<28} {value if value is not None else '（需手动配置）'}")

    # 自动探测无法推导的路径
    detected: dict[str, str] = {}
    wechat = cfg._cached_wechat_file_dir()
    if wechat is not None:
        detected["wechat_file_dir"] = str(wechat)
    if cfg._DEFAULT_NAS_MOUNT.exists():
        detected["company_nas_mount"] = str(cfg._DEFAULT_NAS_MOUNT)

    print("\n== 自动探测 ==")
    print(f"  微信目录：{detected.get('wechat_file_dir', '未找到（用到微信文件的 workflow 需在 paths.local.yaml 手填 wechat_file_dir）')}")
    print(f"  NAS 挂载：{detected.get('company_nas_mount', f'未挂载 {cfg._DEFAULT_NAS_MOUNT}（用到 NAS 的 workflow 需先挂载或改 company_nas_mount）')}")

    local_path = cfg._LOCAL_CONFIG
    if not detected:
        print("\n无需写入 paths.local.yaml（全部路径已自动推导）。")
        return 0
    if local_path.exists() and not args.force:
        print(f"\n{local_path} 已存在，未改动（如需重写加 --force）。")
        return 0

    lines = [
        "# 本机路径覆盖（gitignore，不提交）。仅放无法自动推导的路径。",
        "# 其余路径已从仓库位置 / Path.home() 自动推导，不必在此重复。",
        f"# 由 `run.py setup` 于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 生成。",
        "",
    ]
    for key, value in detected.items():
        lines.append(f"{key}: {value}")
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n已写入 {local_path}")
    if missing_required:
        print(f"⚠️ 仍需手动配置：{'、'.join(missing_required)}")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "workflow":
        return run_workflow(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "runs":
        return list_runs_cli(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "artifacts":
        return search_artifacts_cli(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "sms-challenge":
        return sms_challenge_cli(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "setup":
        return setup_cli(sys.argv[2:])
    args = parse_args()
    if args.list:
        for task_name, task_script in sorted(TASKS.items()):
            print(f"{task_name}\t{task_script}")
        return 0
    context = TaskContext(args.task)
    context.add_input("task_args", args.task_args)
    task_script = TASKS[args.task]
    if not task_script.exists():
        message = f"任务脚本不存在：{task_script}"
        context.add_error(message)
        context_path = context.finish("failed")
        print(message, file=sys.stderr)
        print(f"任务上下文：{context_path}")
        return 2

    command = [choose_python(args.task), str(task_script), *args.task_args]
    context.add_input("command", command)
    started_at = datetime.now().isoformat(timespec="seconds")
    result = subprocess.run(command, text=True, capture_output=True)
    finished_at = datetime.now().isoformat(timespec="seconds")

    parsed_stdout = None
    try:
        parsed_stdout = json.loads(result.stdout) if result.stdout.strip().startswith("{") else None
    except json.JSONDecodeError:
        parsed_stdout = None

    log_payload = {
        "task": args.task,
        "started_at": started_at,
        "finished_at": finished_at,
        "returncode": result.returncode,
        "command": command,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "parsed_stdout": parsed_stdout,
    }
    log_path = write_log(args.task, log_payload)
    dry_run = "--dry-run" in args.task_args
    context.add_output("returncode", result.returncode)
    context.add_output("started_at", started_at)
    context.add_output("finished_at", finished_at)
    context.add_output("log_path", log_path)
    context.add_artifact(log_path, kind="run_log")
    if parsed_stdout is not None:
        context.add_output("parsed_stdout", parsed_stdout)
        for key in ("latest_file", "import_file", "source", "root", "work_dir"):
            if isinstance(parsed_stdout, dict) and parsed_stdout.get(key):
                context.add_artifact(str(parsed_stdout[key]), kind=key)
        if isinstance(parsed_stdout, dict):
            reports = parsed_stdout.get("reports")
            if isinstance(reports, dict):
                for key, value in reports.items():
                    if value:
                        context.add_artifact(str(value), kind=key)
            if parsed_stdout.get("task_log_path"):
                context.add_artifact(str(parsed_stdout["task_log_path"]), kind="task_log")
    if result.returncode != 0:
        context.add_error(
            f"任务退出码：{result.returncode}",
            {
                "stderr_tail": result.stderr[-1000:],
                "stdout_tail": result.stdout[-1000:],
                "traceback": _brief_traceback(result.stderr),
            },
        )
        context_status = "failed"
    elif dry_run:
        context_status = "dry_run_success"
    else:
        context_status = "success"
    context_path = context.finish(context_status)

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    print(f"\n日志：{log_path}")
    print(f"任务上下文：{context_path}")

    return result.returncode


def _brief_traceback(stderr: str) -> str:
    if "Traceback" not in stderr:
        return ""
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return "\n".join(lines[-8:])


if __name__ == "__main__":
    raise SystemExit(main())
