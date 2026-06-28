from __future__ import annotations

import json
import subprocess

import pytest

from clients import ops_cli_client


def _completed(returncode: int, payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ops"],
        returncode=returncode,
        stdout=json.dumps(payload, ensure_ascii=False),
        stderr="",
    )


@pytest.fixture(autouse=True)
def _clear_preflight_cache() -> None:
    ops_cli_client._PREFLIGHTED_PLATFORMS.clear()
    yield
    ops_cli_client._PREFLIGHTED_PLATFORMS.clear()


def test_run_command_defaults_unattended_recovery_env(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])
    monkeypatch.delenv("OPS_UNATTENDED_LOGIN_RECOVERY", raising=False)

    def fake_run(command, **kwargs):
        captured["env"] = kwargs.get("env")
        return _completed(0, {"success": True, "data": {}})

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    ops_cli_client._run_command(["--json", "tmcs", "stock", "query"])

    assert captured["env"]["OPS_UNATTENDED_LOGIN_RECOVERY"] == "1"


def test_run_command_respects_explicit_unattended_override(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])
    monkeypatch.setenv("OPS_UNATTENDED_LOGIN_RECOVERY", "0")

    def fake_run(command, **kwargs):
        captured["env"] = kwargs.get("env")
        return _completed(0, {"success": True, "data": {}})

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    ops_cli_client._run_command(["--json", "tmcs", "stock", "query"])

    assert captured["env"]["OPS_UNATTENDED_LOGIN_RECOVERY"] == "0"


def test_run_command_severs_stdin_for_normal_call(monkeypatch) -> None:
    # 普通自动化调用必须把 ops 子进程 stdin 切到 DEVNULL，避免继承终端 tty 触发 9222 弹窗。
    captured: dict[str, object] = {}
    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])

    def fake_run(command, **kwargs):
        captured["stdin"] = kwargs.get("stdin")
        return _completed(0, {"success": True, "data": {}})

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    ops_cli_client._run_command(["--json", "tmcs", "marketing", "risk-warning", "count"])

    assert captured["stdin"] is subprocess.DEVNULL


def test_run_command_severs_stdin_even_for_interactive_login(monkeypatch) -> None:
    # 桥接是端到端自动化：连 --interactive-login 预检也不放行 tty，避免抢占前台弹窗。
    # --interactive-login 仍驱动平台层静默重沉淀，只是不再把 9222 窗口切前台。
    captured: dict[str, object] = {}
    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])

    def fake_run(command, **kwargs):
        captured["stdin"] = kwargs.get("stdin")
        return _completed(0, {"success": True, "data": {}})

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    ops_cli_client._run_command(["--interactive-login", "--json", "jst", "auth", "ensure"])

    assert captured["stdin"] is subprocess.DEVNULL


def test_run_ops_json_preflights_authenticated_platform_command(monkeypatch) -> None:
    calls: list[list[str]] = []
    results = [
        _completed(0, {"success": True, "data": {"status": "valid"}}),
        _completed(0, {"success": True, "data": {"ok": True}}),
    ]

    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])

    def fake_run(command, **kwargs):
        calls.append(command)
        return results.pop(0)

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    payload = ops_cli_client.run_ops_json(["--json", "jst", "product", "sync"])

    assert payload["success"] is True
    assert calls == [
        ["ops", "--interactive-login", "--json", "jst", "auth", "ensure"],
        ["ops", "--json", "jst", "product", "sync"],
    ]


def test_run_ops_command_returns_typed_result(monkeypatch) -> None:
    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])

    def fake_run(command, **kwargs):
        return _completed(
            0,
            {
                "success": True,
                "platform": "browser",
                "command": "check",
                "data": {
                    "artifacts": ["runtime/context/browser.json"],
                    "context_path": "runtime/context/browser.json",
                    "session_recovery": {"required": False},
                },
            },
        )

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    result = ops_cli_client.run_ops_command(["browser", "check"])

    assert result.success is True
    assert result.platform == "browser"
    assert result.command == "check"
    assert result.artifacts == ["runtime/context/browser.json"]
    assert result.context_path == "runtime/context/browser.json"
    assert result.error_code is None
    assert result.payload["_ops_stdout"]


def test_run_ops_command_raises_typed_error_with_result(monkeypatch) -> None:
    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])

    def fake_run(command, **kwargs):
        return _completed(
            1,
            {
                "success": False,
                "platform": "jst",
                "command": "order remark",
                "data": {
                    "error_code": "AUTH_REQUIRED",
                    "error": "session 不可用",
                    "context_path": "runtime/context/fail.json",
                },
            },
        )

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    with pytest.raises(ops_cli_client.OpsCommandError) as exc_info:
        ops_cli_client.run_ops_command(["jst", "order", "remark", "--dry-run"])

    assert exc_info.value.result.error_code == "AUTH_REQUIRED"
    assert exc_info.value.result.error == "session 不可用"
    assert exc_info.value.result.context_path == "runtime/context/fail.json"


def test_run_ops_json_preflights_each_platform_only_once(monkeypatch) -> None:
    calls: list[list[str]] = []
    results = [
        _completed(0, {"success": True, "data": {"status": "valid"}}),
        _completed(0, {"success": True, "data": {"ok": True}}),
        _completed(0, {"success": True, "data": {"ok": True}}),
    ]

    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])

    def fake_run(command, **kwargs):
        calls.append(command)
        return results.pop(0)

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    ops_cli_client.run_ops_json(["--json", "tmcs", "product", "sync"])
    ops_cli_client.run_ops_json(["--json", "tmcs", "bill", "download"])

    assert calls == [
        ["ops", "--interactive-login", "--json", "tmcs", "auth", "ensure"],
        ["ops", "--json", "tmcs", "product", "sync"],
        ["ops", "--json", "tmcs", "bill", "download"],
    ]


def test_run_ops_json_stops_when_preflight_auth_fails(monkeypatch) -> None:
    calls: list[list[str]] = []
    result = _completed(
        1,
        {
            "success": False,
            "data": {
                "error_code": "AUTH_REQUIRED",
                "error": "session 不可用",
                "context_path": "runtime/context/preflight.json",
            },
        },
    )

    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])

    def fake_run(command, **kwargs):
        calls.append(command)
        return result

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="认证预检失败 \\[AUTH_REQUIRED\\]"):
        ops_cli_client.run_ops_json(["--json", "jst", "order", "label"])

    assert calls == [["ops", "--interactive-login", "--json", "jst", "auth", "ensure"]]


def test_run_ops_json_retries_auth_required_by_default_for_real_command(monkeypatch) -> None:
    calls: list[list[str]] = []
    results = [
        _completed(0, {"success": True, "data": {"status": "valid"}}),
        _completed(
            1,
            {
                "success": False,
                "data": {
                    "error_code": "AUTH_REQUIRED",
                    "error": "session 不可用",
                    "context_path": "runtime/context/first.json",
                },
            },
        ),
        _completed(0, {"success": True, "data": {"ok": True}}),
    ]

    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])
    monkeypatch.setattr(ops_cli_client.sys.stdin, "isatty", lambda: True)

    def fake_run(command, **kwargs):
        calls.append(command)
        return results.pop(0)

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    payload = ops_cli_client.run_ops_json(["--json", "jst", "order", "reimburse"])

    assert payload["success"] is True
    assert calls == [
        ["ops", "--interactive-login", "--json", "jst", "auth", "ensure"],
        ["ops", "--json", "jst", "order", "reimburse"],
        ["ops", "--interactive-login", "--json", "jst", "order", "reimburse"],
    ]


def test_run_ops_json_does_not_retry_without_tty(monkeypatch) -> None:
    calls: list[list[str]] = []
    results = [
        _completed(0, {"success": True, "data": {"status": "valid"}}),
        _completed(
            1,
            {
                "success": False,
                "data": {
                    "error_code": "AUTH_REQUIRED",
                    "error": "session 不可用",
                    "context_path": "runtime/context/first.json",
                },
            },
        ),
    ]

    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])
    monkeypatch.setattr(ops_cli_client.sys.stdin, "isatty", lambda: False)

    def fake_run(command, **kwargs):
        calls.append(command)
        return results.pop(0)

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    try:
        ops_cli_client.run_ops_json(["--json", "jst", "order", "reimburse"], interactive_recovery=True)
    except RuntimeError as exc:
        assert "AUTH_REQUIRED" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert calls == [
        ["ops", "--interactive-login", "--json", "jst", "auth", "ensure"],
        ["ops", "--json", "jst", "order", "reimburse"],
    ]


def test_run_ops_json_does_not_retry_dry_run_by_default(monkeypatch) -> None:
    calls: list[list[str]] = []
    result = _completed(
        1,
        {
            "success": False,
            "data": {
                "error_code": "AUTH_REQUIRED",
                "error": "session 不可用",
                "context_path": "runtime/context/first.json",
            },
        },
    )

    monkeypatch.setattr(ops_cli_client, "_command_prefix", lambda: ["ops"])
    monkeypatch.setattr(ops_cli_client.sys.stdin, "isatty", lambda: True)

    def fake_run(command, **kwargs):
        calls.append(command)
        return result

    monkeypatch.setattr(ops_cli_client.subprocess, "run", fake_run)

    try:
        ops_cli_client.run_ops_json(["--json", "jst", "product", "sync", "--dry-run"])
    except RuntimeError as exc:
        assert "AUTH_REQUIRED" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")

    assert calls == [["ops", "--json", "jst", "product", "sync", "--dry-run"]]
