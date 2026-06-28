from __future__ import annotations

import pytest

from ops_cli import capabilities
from ops_cli.capabilities import CapabilitySpec, bind_capability_execution


def _spec(policy: str = "interactive_if_tty") -> CapabilitySpec:
    return CapabilitySpec(id="t.x", platform="t", command="x", recovery_policy=policy)


def test_unattended_enables_recovery_without_tty(monkeypatch) -> None:
    monkeypatch.setattr(capabilities.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("OPS_UNATTENDED_LOGIN_RECOVERY", "1")
    with bind_capability_execution(_spec()) as execution:
        assert execution.allow_recovery is True


def test_unattended_off_keeps_fail_fast_without_tty(monkeypatch) -> None:
    monkeypatch.setattr(capabilities.sys.stdin, "isatty", lambda: False)
    monkeypatch.delenv("OPS_UNATTENDED_LOGIN_RECOVERY", raising=False)
    with bind_capability_execution(_spec()) as execution:
        assert execution.allow_recovery is False


def test_unattended_never_recovers_in_dry_run(monkeypatch) -> None:
    monkeypatch.setattr(capabilities.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("OPS_UNATTENDED_LOGIN_RECOVERY", "1")
    with bind_capability_execution(_spec(), dry_run=True) as execution:
        assert execution.allow_recovery is False


def test_unattended_never_recovers_for_never_policy(monkeypatch) -> None:
    monkeypatch.setattr(capabilities.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("OPS_UNATTENDED_LOGIN_RECOVERY", "1")
    with bind_capability_execution(_spec("never")) as execution:
        assert execution.allow_recovery is False


def test_explicit_no_interactive_overrides_unattended(monkeypatch) -> None:
    # 显式 --no-interactive-login 是硬性意图，即便开关开启也不恢复
    monkeypatch.setattr(capabilities.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("OPS_UNATTENDED_LOGIN_RECOVERY", "1")
    with bind_capability_execution(_spec(), interactive_login=False) as execution:
        assert execution.allow_recovery is False


@pytest.mark.parametrize("value", ["0", "false", "off", ""])
def test_unattended_off_values(monkeypatch, value: str) -> None:
    monkeypatch.setattr(capabilities.sys.stdin, "isatty", lambda: False)
    monkeypatch.setenv("OPS_UNATTENDED_LOGIN_RECOVERY", value)
    with bind_capability_execution(_spec()) as execution:
        assert execution.allow_recovery is False
