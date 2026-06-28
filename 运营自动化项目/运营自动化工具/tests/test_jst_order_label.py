from __future__ import annotations

import sys

from tasks.jst_order_label import main as order_label


def test_main_routes_to_workflow_without_direct_ops_call(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(order_label, "_run_workflow", lambda args: calls.append(list(args)) or 0, raising=False)
    monkeypatch.setattr(
        order_label,
        "run_ops_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("旧入口不应直接请求 Ops-Cli")),
    )
    monkeypatch.setattr(sys, "argv", ["jst_order_label", "--dry-run", "--limit", "1", "--order-id", "ORDER001"])

    assert order_label.main() == 0
    assert calls == [["jst_order_label", "--dry-run", "--limit", "1", "--order-id", "ORDER001"]]
