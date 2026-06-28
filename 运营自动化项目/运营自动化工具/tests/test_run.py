from __future__ import annotations

import sys
from pathlib import Path

import os

import run
from run import (
    _maybe_reexec_in_business_venv,
    choose_python,
    python_candidates,
    python_has_modules,
)


def test_python_candidates_starts_with_sys_executable() -> None:
    candidates = python_candidates()
    assert candidates[0] == Path(sys.executable)


def test_python_candidates_no_duplicates() -> None:
    candidates = python_candidates()
    assert len(candidates) == len(set(str(c) for c in candidates))


def test_python_has_modules_returns_false_for_missing() -> None:
    assert python_has_modules(Path("/nonexistent/python3"), ("nonexistent_module_xyz",)) is False


def test_choose_python_returns_sys_executable_fallback(monkeypatch) -> None:
    monkeypatch.setattr("run.python_candidates", lambda: [])
    result = choose_python("tag_jst_brush_orders")
    assert result == sys.executable


def test_bootstrap_skips_when_already_bootstrapped(monkeypatch) -> None:
    monkeypatch.setenv("OPS_BUSINESS_VENV_BOOTSTRAPPED", "1")
    called = []
    monkeypatch.setattr(os, "execv", lambda *a: called.append(a))
    _maybe_reexec_in_business_venv()
    assert called == []


def test_bootstrap_skips_when_venv_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPS_BUSINESS_VENV_BOOTSTRAPPED", raising=False)
    # __file__ 指向 tmp_path 下，确保 .venv 不存在
    monkeypatch.setattr(run, "__file__", str(tmp_path / "run.py"))
    called = []
    monkeypatch.setattr(os, "execv", lambda *a: called.append(a))
    _maybe_reexec_in_business_venv()
    assert called == []


def test_bootstrap_reexecs_into_venv(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPS_BUSINESS_VENV_BOOTSTRAPPED", raising=False)
    fake_root = tmp_path
    venv_python = fake_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("#!/bin/sh\n")
    monkeypatch.setattr(run, "__file__", str(fake_root / "run.py"))
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(sys, "argv", ["run.py", "--list"])
    called = []
    monkeypatch.setattr(os, "execv", lambda *a: called.append(a))
    _maybe_reexec_in_business_venv()
    assert len(called) == 1
    exe, argv = called[0]
    assert exe == str(venv_python)
    assert argv[0] == str(venv_python)
    assert argv[-1] == "--list"
    assert os.environ.get("OPS_BUSINESS_VENV_BOOTSTRAPPED") == "1"


def test_task_required_modules_matches_expected() -> None:
    from core.task_registry import task_required_modules

    modules = task_required_modules()
    assert modules["buyer_show"] == ("openpyxl", "PIL")
    assert modules["tag_jst_brush_orders"] == ()
    assert modules["jst_brush_reimburse_workorder"] == ("requests", "openpyxl")
    assert modules["按摩椅订单自动备注"] == ("openpyxl",)
    assert modules["process_maochao_bills"] == ("openpyxl",)
