from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator


@dataclass(frozen=True)
class CapabilitySpec:
    id: str
    platform: str
    command: str
    scenes: tuple[str, ...] = ()
    recovery_policy: str = "interactive_if_tty"
    dry_run_policy: str = "check_only"
    artifact_types: tuple[str, ...] = ()


@dataclass
class SessionRecoveryState:
    required: bool = False
    interactive: bool = False
    scenes_refreshed: list[str] = field(default_factory=list)
    retry_count: int = 0

    def mark_required(self) -> None:
        self.required = True

    def mark_refreshed(self, scene: str) -> None:
        self.required = True
        if scene not in self.scenes_refreshed:
            self.scenes_refreshed.append(scene)
        self.retry_count = max(self.retry_count, 1)

    def as_dict(self) -> dict[str, object]:
        return {
            "required": self.required,
            "interactive": self.interactive,
            "scenes_refreshed": list(self.scenes_refreshed),
            "retry_count": self.retry_count,
        }


@dataclass(frozen=True)
class CapabilityExecution:
    spec: CapabilitySpec
    dry_run: bool
    interactive_login: bool
    allow_recovery: bool
    recovery: SessionRecoveryState


# Dynamic registries — populated by register_capabilities() during platform discovery
_CAPABILITIES: dict[str, CapabilitySpec] = {}
_COMMAND_INDEX: dict[tuple[str, str], CapabilitySpec] = {}
_CURRENT_EXECUTION: ContextVar[CapabilityExecution | None] = ContextVar("ops_capability_execution", default=None)


def register_capabilities(specs: list[CapabilitySpec]) -> None:
    """Register a list of CapabilitySpec instances into the global registries."""
    for spec in specs:
        _CAPABILITIES[spec.id] = spec
        _COMMAND_INDEX[(spec.platform, spec.command)] = spec


def capability_ids() -> set[str]:
    return set(_CAPABILITIES)


def get_capability(identifier: str) -> CapabilitySpec:
    return _CAPABILITIES[identifier]


def capability_for_command(platform: str, command: str) -> CapabilitySpec:
    return _COMMAND_INDEX[(platform, command)]


def current_capability_execution() -> CapabilityExecution | None:
    return _CURRENT_EXECUTION.get()


def require_interactive_recovery(scene: str) -> None:
    execution = current_capability_execution()
    if execution is not None and not execution.allow_recovery:
        execution.recovery.mark_required()
        raise RuntimeError(f"session 不可用：{scene} 需要交互登录恢复，当前执行模式禁止自动恢复。")


def mark_scene_refreshed(scene: str) -> None:
    execution = current_capability_execution()
    if execution is not None:
        execution.recovery.mark_refreshed(scene)


def recovery_must_fail_fast() -> bool:
    execution = current_capability_execution()
    return bool(execution is not None and not execution.dry_run and not execution.allow_recovery)


def _interactive_default() -> bool:
    return bool(sys.stdin.isatty())


def _unattended_recovery_enabled() -> bool:
    """无人值守登录恢复开关。

    定时/后台(非 tty)运行时，允许平台自行从 9222 SessionHub 浏览器重沉淀登录态，
    无需真人在终端。仅在未显式指定 --interactive-login/--no-interactive-login 时生效；
    dry-run 与 recovery_policy="never" 的能力始终不恢复。
    业务层桥接 (运营自动化工具/clients/ops_cli_client.py) 默认给子进程置 1。
    """
    return os.environ.get("OPS_UNATTENDED_LOGIN_RECOVERY", "").strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def bind_capability_execution(
    spec: CapabilitySpec,
    *,
    dry_run: bool = False,
    interactive_login: bool | None = None,
) -> Iterator[CapabilityExecution]:
    if interactive_login is None:
        # 未显式指定时：真人终端(tty) 或 无人值守开关 任一成立即可恢复
        interactive = _interactive_default() or _unattended_recovery_enabled()
    else:
        # 显式 --interactive-login / --no-interactive-login 为硬性意图，覆盖一切
        interactive = interactive_login
    allow_recovery = (
        not dry_run
        and interactive
        and spec.recovery_policy in {"interactive_if_tty", "explicit"}
    )
    recovery = SessionRecoveryState(interactive=allow_recovery)
    execution = CapabilityExecution(
        spec=spec,
        dry_run=dry_run,
        interactive_login=interactive,
        allow_recovery=allow_recovery,
        recovery=recovery,
    )
    token = _CURRENT_EXECUTION.set(execution)
    try:
        yield execution
    finally:
        _CURRENT_EXECUTION.reset(token)
