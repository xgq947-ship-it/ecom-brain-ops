from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_chrome_cdp():
    path = Path(__file__).resolve().parents[1] / "sessionhub" / "scene" / "chrome_cdp.py"
    spec = importlib.util.spec_from_file_location("chrome_cdp_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hide_chrome_waits_until_dedicated_window_is_hidden(monkeypatch) -> None:
    chrome_cdp = _load_chrome_cdp()
    states = iter([
        {"visible": True, "frontmost": True, "windows": 1},
        {"visible": False, "frontmost": False, "windows": 1},
    ])

    monkeypatch.setattr(chrome_cdp, "_instance_pid", lambda: 9222)
    monkeypatch.setattr(chrome_cdp, "_system_events", lambda *statements: True)
    monkeypatch.setattr(chrome_cdp, "_process_window_state", lambda pid: next(states))

    ok, message = chrome_cdp.hide_chrome()

    assert ok is True
    assert "隐藏到后台" in message


def test_hide_chrome_reports_failure_when_window_remains_visible(monkeypatch) -> None:
    chrome_cdp = _load_chrome_cdp()

    monkeypatch.setattr(chrome_cdp, "_instance_pid", lambda: 9222)
    monkeypatch.setattr(chrome_cdp, "_system_events", lambda *statements: True)
    monkeypatch.setattr(
        chrome_cdp,
        "_process_window_state",
        lambda pid: {"visible": True, "frontmost": False, "windows": 1},
    )

    ok, message = chrome_cdp.hide_chrome(max_wait_seconds=0.01, poll_interval=0.001)

    assert ok is False
    assert "未确认隐藏" in message


def test_start_chrome_restarts_headful_instance_when_headless_requested(monkeypatch, tmp_path) -> None:
    chrome_cdp = _load_chrome_cdp()
    launches = []
    stops = []
    chrome_bin = tmp_path / "Google Chrome"
    chrome_bin.write_text("", encoding="utf-8")

    monkeypatch.setattr(chrome_cdp, "CHROME_BIN", chrome_bin)
    monkeypatch.setattr(chrome_cdp, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(chrome_cdp, "_instance_pid", lambda: 9222)
    monkeypatch.setattr(chrome_cdp, "_instance_is_headless", lambda pid: False)
    monkeypatch.setattr(chrome_cdp, "stop_chrome", lambda: stops.append(True) or (True, "stopped"))
    monkeypatch.setattr(chrome_cdp, "hide_chrome", lambda **kwargs: (True, "hidden"))
    checks = iter([(True, "running"), (True, "ready")])
    monkeypatch.setattr(chrome_cdp, "check_cdp", lambda: next(checks))

    class _Popen:
        def __init__(self, cmd, **kwargs):
            launches.append(cmd)

    monkeypatch.setattr(chrome_cdp.subprocess, "Popen", _Popen)

    ok, _ = chrome_cdp.start_chrome(headless=True)

    assert ok is True
    assert stops == [True]
    assert launches
    assert "--headless=new" in launches[0]


def test_start_chrome_restarts_headless_instance_for_interactive_headful(monkeypatch, tmp_path) -> None:
    chrome_cdp = _load_chrome_cdp()
    launches = []
    stops = []
    chrome_bin = tmp_path / "Google Chrome"
    chrome_bin.write_text("", encoding="utf-8")

    monkeypatch.setattr(chrome_cdp, "CHROME_BIN", chrome_bin)
    monkeypatch.setattr(chrome_cdp, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(chrome_cdp, "_instance_pid", lambda: 9222)
    monkeypatch.setattr(chrome_cdp, "_instance_is_headless", lambda pid: True)
    monkeypatch.setattr(chrome_cdp, "_foreground_allowed", lambda: True)
    monkeypatch.setattr(chrome_cdp, "stop_chrome", lambda: stops.append(True) or (True, "stopped"))
    monkeypatch.setattr(chrome_cdp, "hide_chrome", lambda **kwargs: (True, "hidden"))
    checks = iter([(True, "running"), (True, "ready")])
    monkeypatch.setattr(chrome_cdp, "check_cdp", lambda: next(checks))

    class _Popen:
        def __init__(self, cmd, **kwargs):
            launches.append(cmd)

    monkeypatch.setattr(chrome_cdp.subprocess, "Popen", _Popen)

    ok, _ = chrome_cdp.start_chrome()

    assert ok is True
    assert stops == [True]
    assert launches
    assert "--headless=new" not in launches[0]


def test_start_chrome_defaults_headless_when_background_no_tty(monkeypatch, tmp_path) -> None:
    """无实例 + 非前台 + 无 tty（Hermes/后台）时，默认无头静默拉起。"""
    chrome_cdp = _load_chrome_cdp()
    launches = []
    chrome_bin = tmp_path / "Google Chrome"
    chrome_bin.write_text("", encoding="utf-8")

    monkeypatch.setattr(chrome_cdp, "CHROME_BIN", chrome_bin)
    monkeypatch.setattr(chrome_cdp, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(chrome_cdp, "_foreground_allowed", lambda: False)
    # 首次 check 无实例，启动后再 check 就绪。
    checks = iter([(False, "down"), (True, "ready")])
    monkeypatch.setattr(chrome_cdp, "check_cdp", lambda: next(checks))

    class _Popen:
        def __init__(self, cmd, **kwargs):
            launches.append(cmd)

    monkeypatch.setattr(chrome_cdp.subprocess, "Popen", _Popen)

    ok, _ = chrome_cdp.start_chrome()

    assert ok is True
    assert launches
    assert "--headless=new" in launches[0]
    assert "--window-size=1440,900" in launches[0]


def test_start_chrome_stays_headful_when_foreground_requested(monkeypatch, tmp_path) -> None:
    """显式 foreground（桌面 app / 登录场景）时不被默认无头覆盖。"""
    chrome_cdp = _load_chrome_cdp()
    launches = []
    chrome_bin = tmp_path / "Google Chrome"
    chrome_bin.write_text("", encoding="utf-8")

    monkeypatch.setattr(chrome_cdp, "CHROME_BIN", chrome_bin)
    monkeypatch.setattr(chrome_cdp, "PROFILE_DIR", tmp_path / "profile")
    monkeypatch.setattr(chrome_cdp, "_foreground_allowed", lambda: False)
    monkeypatch.setattr(chrome_cdp, "bring_chrome_to_front", lambda: (True, "front"))
    checks = iter([(False, "down"), (True, "ready")])
    monkeypatch.setattr(chrome_cdp, "check_cdp", lambda: next(checks))

    class _Popen:
        def __init__(self, cmd, **kwargs):
            launches.append(cmd)

    monkeypatch.setattr(chrome_cdp.subprocess, "Popen", _Popen)

    ok, _ = chrome_cdp.start_chrome(foreground=True)

    assert ok is True
    assert launches
    assert "--headless=new" not in launches[0]
