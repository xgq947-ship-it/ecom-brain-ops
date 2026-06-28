#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path


AUTOMATION_ROOT = Path.home() / "Desktop" / "电商Brain" / "02-运营店铺" / "运营自动化工具"
AUTOMATION_PYTHON = AUTOMATION_ROOT / ".venv/bin/python"
AUTOMATION_RUN = AUTOMATION_ROOT / "run.py"
HERMES_HOME = Path.home() / ".hermes"
HERMES_ENV = HERMES_HOME / ".env"
HERMES_AGENT = HERMES_HOME / "hermes-agent"
HERMES_PYTHON = HERMES_AGENT / "venv/bin/python3"
DEFAULT_FEISHU_TARGET = "feishu:oc_7159a0c36bedd82df2eafec9fa5a25b1"
ERROR_FEISHU_TARGET = "feishu:oc_eb4b4846c2b7d10df1099e5aa75328a3"
WEEKDAY_CN = ["一", "二", "三", "四", "五", "六", "日"]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _extract_first_json_object(text: str) -> dict:
    stripped = text.strip()
    if not stripped:
        raise RuntimeError("workflow 无输出")
    try:
        payload, _index = json.JSONDecoder().raw_decode(stripped)
    except JSONDecodeError as exc:
        raise RuntimeError(f"workflow 返回非 JSON：{stripped[:500]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("workflow JSON 响应不是对象")
    return payload


def _workflow_python() -> Path:
    return AUTOMATION_PYTHON if AUTOMATION_PYTHON.exists() else Path(sys.executable)


def _run_profit_snapshot_workflow(*args: str, timeout: int = 240) -> dict:
    if not AUTOMATION_RUN.exists():
        raise RuntimeError(f"未找到 workflow 入口：{AUTOMATION_RUN}")
    env = {**os.environ, "OPS_UNATTENDED_LOGIN_RECOVERY": "1"}
    result = subprocess.run(
        [str(_workflow_python()), str(AUTOMATION_RUN), "workflow", "jst_shop_profit_snapshot", *args],
        cwd=str(AUTOMATION_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
        timeout=timeout,
    )
    output = result.stdout.strip()
    if result.returncode != 0:
        raise RuntimeError(f"利润 workflow 执行失败 (rc={result.returncode})：{result.stderr.strip() or output}")
    run_payload = _extract_first_json_object(output)
    output_path = str((run_payload.get("outputs") or {}).get("output_path") or "").strip()
    if not output_path:
        raise RuntimeError("利润 workflow 未返回 output_path")
    snapshot_path = Path(output_path)
    if not snapshot_path.exists():
        raise RuntimeError(f"利润 workflow 快照不存在：{snapshot_path}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return {"success": True, "data": snapshot, "workflow": run_payload}


def run_profit_query() -> dict:
    payload = _run_profit_snapshot_workflow()
    if (payload.get("data") or {}).get("profit") is None:
        raise RuntimeError("利润 workflow 未返回 profit")
    return payload


def format_message(payload: dict) -> str:
    data = payload["data"]
    profit = float(data["profit"])
    date = datetime.strptime(str(data["date"]), "%Y-%m-%d")
    weekday = WEEKDAY_CN[date.weekday()]
    store = str(data.get("store") or "猫超").replace("（肖国清）", "")
    metric = str(data.get("metric_field") or "经营利润")
    return "\n".join(
        [
            "📊 猫超昨日利润日报",
            f"📅 {date.month}月{date.day}日（周{weekday}）",
            f"🏪 {store}",
            "━━━━━━━━━━━━━━━━━",
            f"💰 {metric}  ¥{profit:,.2f}",
        ]
    )


def send_feishu(message: str, *, target: str) -> dict:
    load_env(HERMES_ENV)
    if not HERMES_PYTHON.exists():
        raise RuntimeError(f"Hermes Python 不存在：{HERMES_PYTHON}")
    script = (
        "import json, os, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from tools.send_message_tool import send_message_tool\n"
        "result = send_message_tool({'target': sys.argv[2], 'message': sys.stdin.read()})\n"
        "print(result)\n"
    )
    completed = subprocess.run(
        [str(HERMES_PYTHON), "-c", script, str(HERMES_AGENT), target],
        cwd=str(HERMES_AGENT),
        env=os.environ.copy(),
        input=message,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Hermes 飞书发送失败")
    raw = completed.stdout.strip().splitlines()[-1]
    result = json.loads(raw)
    if not result.get("success"):
        raise RuntimeError(str(result.get("error") or raw))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Send JST daily profit to Feishu through Hermes.")
    parser.add_argument("--target", default=os.getenv("DAILY_PROFIT_FEISHU_TARGET", DEFAULT_FEISHU_TARGET))
    parser.add_argument("--no-send", action="store_true", help="Only query and print the message.")
    args = parser.parse_args()

    try:
        payload = run_profit_query()
        message = format_message(payload)
        send_result = None if args.no_send else send_feishu(message, target=args.target)
        print(json.dumps({"success": True, "message": message, "send_result": send_result}, ensure_ascii=False))
        return 0
    except Exception as exc:
        failure_message = "\n".join(
            [
                "猫超日利润自动推送失败",
                f"错误：{exc}",
                f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ]
        )
        if not args.no_send:
            try:
                send_feishu(failure_message, target=ERROR_FEISHU_TARGET)
            except Exception as send_exc:
                print(json.dumps({"success": False, "error": str(exc), "send_error": str(send_exc)}, ensure_ascii=False))
                return 1
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

