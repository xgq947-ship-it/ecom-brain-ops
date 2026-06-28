from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.config_loader import get_path


_AUTH_PLATFORMS = {"jst", "tmcs"}
_PREFLIGHTED_PLATFORMS: set[str] = set()


@dataclass(frozen=True)
class OpsCommandResult:
    success: bool
    platform: str = ""
    command: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OpsCommandResult":
        data = payload.get("data")
        if not isinstance(data, dict):
            data = {}
        return cls(
            success=bool(payload.get("success")),
            platform=str(payload.get("platform") or ""),
            command=str(payload.get("command") or ""),
            data=data,
            payload=payload,
        )

    @property
    def error_code(self) -> str | None:
        value = self.data.get("error_code")
        return str(value) if value else None

    @property
    def error(self) -> str | None:
        value = self.data.get("error")
        return str(value) if value else None

    @property
    def context_path(self) -> str | None:
        value = self.data.get("context_path")
        return str(value) if value else None

    @property
    def artifacts(self) -> list[str]:
        artifacts = self.data.get("artifacts")
        if not isinstance(artifacts, list):
            return []
        return [str(item) for item in artifacts if item]

    @property
    def session_recovery(self) -> dict[str, Any]:
        value = self.data.get("session_recovery")
        return value if isinstance(value, dict) else {}


class OpsCommandError(RuntimeError):
    def __init__(self, message: str, result: OpsCommandResult) -> None:
        super().__init__(message)
        self.result = result


def ops_cli_root() -> Path:
    try:
        configured = get_path("ops_cli_root")
    except KeyError:
        configured = Path(__file__).resolve().parents[2].parent / "Ops-Cli"
    return Path(configured).expanduser().resolve()


def ops_cli_bin() -> Path:
    try:
        configured = get_path("ops_cli_bin")
    except KeyError:
        configured = ops_cli_root() / ".venv" / "bin" / "ops"
    return Path(configured).expanduser().resolve()


def _command_prefix() -> list[str]:
    root = ops_cli_root()
    if not root.is_dir():
        raise FileNotFoundError(f"Ops-Cli 项目路径不存在：{root}")
    binary = ops_cli_bin()
    if binary.exists():
        return [str(binary)]

    venv_python = root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return [str(venv_python), "-m", "ops_cli.cli"]

    return [sys.executable, "-m", "ops_cli.cli"]


def _run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    # 业务层(workflow/定时任务)默认以无人值守模式调用平台：登录态失效时由 Ops-Cli
    # 自行从 9222 重沉淀恢复，不依赖真人在终端。dry-run 与 never 策略在平台层仍不恢复。
    # 用户可在环境里显式 export OPS_UNATTENDED_LOGIN_RECOVERY=0 关闭。
    env = dict(os.environ)
    env.setdefault("OPS_UNATTENDED_LOGIN_RECOVERY", "1")
    # 弹窗(把 9222 Chrome 切前台)在 Ops-Cli 侧由 sys.stdin.isatty() 守卫。capture_output
    # 只接管 stdout/stderr，stdin 仍继承父进程：当 workflow 在带 tty 的终端(手动 run.py /
    # Claude / 交互式 Hermes)里被触发时，tty 会一路传到 ops 子进程，会话失效命中登录页就会
    # 误把窗口弹到前台。业务桥接是端到端的自动化(连 --interactive-login 预检也是桥接自动加的，
    # 不是真人主动要登录)，一律把子进程 stdin 切到 DEVNULL → ops 永远看不到 tty → 守卫静默
    # 不弹。--interactive-login 仍照常驱动“静默重沉淀”(9222 仍在线即可恢复)；9222 真的掉登录
    # 时本次调用直接失败 → 由上层飞书告警，人工再登录，不再抢占前台。真人想交互登录请直接在
    # 终端跑 ops / learn(不经桥接，自带真 tty，弹窗+等待登录流程不受影响)。
    return subprocess.run(
        [*_command_prefix(), *args],
        cwd=ops_cli_root(),
        text=True,
        capture_output=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )


def _parse_payload(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if not stdout:
        raise RuntimeError("Ops-Cli 未返回 JSON 输出")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ops-Cli 返回非 JSON：{stdout[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Ops-Cli JSON 响应不是对象")
    payload["_ops_stdout"] = stdout
    payload["_ops_stderr"] = stderr
    return payload


def _should_retry_interactively(payload: dict[str, Any], *, interactive_recovery: bool) -> bool:
    if not interactive_recovery or not sys.stdin.isatty():
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    return str(data.get("error_code") or "") == "AUTH_REQUIRED"


def _default_interactive_recovery(args: list[str]) -> bool:
    if "--dry-run" in args:
        return False
    return not ("auth" in args and "check" in args)


def _preflight_platform(args: list[str], *, allow_recovery: bool) -> str | None:
    if not allow_recovery or "--dry-run" in args:
        return None
    for index, part in enumerate(args):
        if part not in _AUTH_PLATFORMS:
            continue
        if index + 1 < len(args) and args[index + 1] == "auth":
            return None
        return part
    return None


def _raise_command_failure(payload: dict[str, Any], *, prefix: str = "Ops-Cli 执行失败") -> None:
    result = OpsCommandResult.from_payload(payload)
    raise OpsCommandError(
        f"{prefix} [{result.error_code or 'UNKNOWN'}]："
        f"{result.error or '未知错误'}；context={result.context_path or ''}",
        result,
    )


def preflight_platform_auth(platform: str) -> None:
    if platform not in _AUTH_PLATFORMS:
        raise ValueError(f"不支持认证预检的平台：{platform}")
    if platform in _PREFLIGHTED_PLATFORMS:
        return
    completed = _run_command(["--interactive-login", "--json", platform, "auth", "ensure"])
    payload = _parse_payload(completed)
    if completed.returncode != 0:
        _raise_command_failure(payload, prefix=f"{platform} 认证预检失败")
    _PREFLIGHTED_PLATFORMS.add(platform)


def run_ops_command(args: list[str], *, interactive_recovery: bool | None = None) -> OpsCommandResult:
    json_args = args if "--json" in args else ["--json", *args]
    allow_interactive_recovery = (
        _default_interactive_recovery(json_args)
        if interactive_recovery is None
        else interactive_recovery and _default_interactive_recovery(json_args)
    )
    platform = _preflight_platform(json_args, allow_recovery=allow_interactive_recovery)
    if platform is not None:
        preflight_platform_auth(platform)
    completed = _run_command(json_args)
    payload = _parse_payload(completed)
    if completed.returncode != 0 and isinstance(payload, dict) and _should_retry_interactively(
        payload,
        interactive_recovery=allow_interactive_recovery,
    ):
        completed = _run_command(["--interactive-login", *json_args])
        payload = _parse_payload(completed)
    if completed.returncode != 0:
        if isinstance(payload, dict):
            _raise_command_failure(payload)
        raise RuntimeError("Ops-Cli 执行失败且响应结构不是对象")
    return OpsCommandResult.from_payload(payload)


def run_ops_json(args: list[str], *, interactive_recovery: bool | None = None) -> dict[str, Any]:
    result = run_ops_command(args, interactive_recovery=interactive_recovery)
    return result.payload
