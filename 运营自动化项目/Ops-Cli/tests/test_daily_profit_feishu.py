from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "send_daily_profit_feishu.py"
SPEC = importlib.util.spec_from_file_location("send_daily_profit_feishu", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_format_message_daily_profit() -> None:
    payload = {
        "data": {
            "date": "2026-06-17",
            "store": "（猫超）福安市启明工贸有限公司（肖国清）",
            "profit": 9393.03,
            "metric_field": "经营利润",
        }
    }

    assert MODULE.format_message(payload) == (
        "📊 猫超昨日利润日报\n"
        "📅 6月17日（周三）\n"
        "🏪 （猫超）福安市启明工贸有限公司\n"
        "━━━━━━━━━━━━━━━━━\n"
        "💰 经营利润  ¥9,393.03"
    )


def test_run_profit_query_calls_profit_snapshot_workflow(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "profit.json"
    output.write_text(
        '{"date":"2026-06-17","store":"（猫超）福安市启明工贸有限公司（肖国清）","profit":9393.03,"metric_field":"经营利润"}',
        encoding="utf-8",
    )
    seen = {}

    class Completed:
        returncode = 0
        stdout = '{"success": true, "outputs": {"output_path": "' + str(output) + '"}}'
        stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs.get("cwd")
        return Completed()

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    payload = MODULE.run_profit_query()

    assert seen["cwd"] == str(MODULE.AUTOMATION_ROOT)
    assert "jst_shop_profit_snapshot" in seen["command"]
    assert "--month" not in seen["command"]
    assert "ops" not in " ".join(seen["command"])
    assert payload["success"] is True
    assert payload["data"]["profit"] == 9393.03


def test_send_feishu_uses_hermes_python_subprocess(monkeypatch, tmp_path: Path) -> None:
    hermes_python = tmp_path / "python3"
    hermes_python.write_text("#!/bin/sh\n", encoding="utf-8")
    hermes_agent = tmp_path / "hermes-agent"
    hermes_agent.mkdir()
    seen = {}

    class Completed:
        returncode = 0
        stdout = '{"success": true, "platform": "feishu"}\n'
        stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs.get("cwd")
        seen["input"] = kwargs.get("input")
        return Completed()

    monkeypatch.setattr(MODULE, "HERMES_PYTHON", hermes_python)
    monkeypatch.setattr(MODULE, "HERMES_AGENT", hermes_agent)
    monkeypatch.setattr(MODULE, "load_env", lambda _path: None)
    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    result = MODULE.send_feishu("hello", target="feishu:demo")

    assert result == {"success": True, "platform": "feishu"}
    assert seen["command"][0] == str(hermes_python)
    assert seen["cwd"] == str(hermes_agent)
    assert seen["input"] == "hello"
