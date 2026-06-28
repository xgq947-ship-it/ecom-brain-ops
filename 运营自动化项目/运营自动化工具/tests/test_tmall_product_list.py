from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tasks.tmall_product_list import main as tmall_product_main


def test_parse_args_no_longer_accepts_work_dir(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["tmall_product_list", "--work-dir", "/tmp/legacy-workdir"],
    )

    with pytest.raises(SystemExit) as exc_info:
        tmall_product_main.parse_args()

    assert exc_info.value.code == 2


def test_main_routes_to_workflow_without_direct_ops_call(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["tmall_product_list", "--dry-run", "--skip-auto-download"])
    monkeypatch.setattr(tmall_product_main, "_run_workflow", lambda args: calls.append(list(args)) or 0, raising=False)
    monkeypatch.setattr(
        tmall_product_main,
        "run_ops_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("旧入口不应直接请求 Ops-Cli")),
    )

    assert tmall_product_main.main() == 0
    assert calls == [["tmall_product_list", "--dry-run", "--skip-auto-download"]]
