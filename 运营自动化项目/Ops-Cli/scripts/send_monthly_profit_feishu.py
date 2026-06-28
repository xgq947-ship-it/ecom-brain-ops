#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from json import JSONDecodeError
from pathlib import Path


AUTOMATION_ROOT = Path.home() / "Desktop" / "电商Brain" / "02-运营店铺" / "运营自动化工具"
AUTOMATION_PYTHON = AUTOMATION_ROOT / ".venv/bin/python"
AUTOMATION_RUN = AUTOMATION_ROOT / "run.py"
HERMES_HOME = Path.home() / ".hermes"
HERMES_ENV = HERMES_HOME / ".env"
HERMES_AGENT = HERMES_HOME / "hermes-agent"
HERMES_PYTHON = HERMES_AGENT / "venv/bin/python3"
DEFAULT_FEISHU_TARGET = "feishu:oc_7159a0c36bedd82df2eafec9fa5a25b1"   # 猫超群
ERROR_FEISHU_TARGET = "feishu:oc_eb4b4846c2b7d10df1099e5aa75328a3"     # DM（报错）


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def previous_month(today: date | None = None) -> str:
    current = today or date.today()
    previous_month_last_day = current.replace(day=1) - timedelta(days=1)
    return previous_month_last_day.strftime("%Y-%m")


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


def _run_profit_snapshot_workflow(*args: str, timeout: int = 300) -> dict:
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
    output_path = ((run_payload.get("outputs") or {}).get("output_path") or "").strip()
    if not output_path:
        raise RuntimeError("利润 workflow 未返回 output_path")
    snapshot_path = Path(output_path)
    if not snapshot_path.exists():
        raise RuntimeError(f"利润 workflow 快照不存在：{snapshot_path}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    return {"success": True, "data": snapshot, "workflow": run_payload}


def _profit_ok(payload: dict) -> bool:
    """success 且确实拿到 profit 字段——profit scene cookie 过期会「无报错空数据」。"""
    if not payload.get("success"):
        return False
    return (payload.get("data") or {}).get("profit") is not None


def _profit_error(payload: dict) -> str:
    data = payload.get("data") or {}
    return str(
        payload.get("error")
        or data.get("error")
        or data.get("error_code")
        or "未找到经营利润字段（疑似 profit scene cookie 过期）"
    )


def run_profit_query(month: str) -> dict:
    payload = _run_profit_snapshot_workflow("--month", month)
    if _profit_ok(payload):
        return payload
    raise RuntimeError(f"利润 workflow 返回异常：{_profit_error(payload)}")


def format_message(payload: dict) -> str:
    data = payload["data"]
    month = str(data["month"])
    month_date = datetime.strptime(month, "%Y-%m")
    store = str(data.get("store") or "猫超").replace("（肖国清）", "")
    metric = str(data.get("metric_field") or "经营利润")
    profit = float(data["profit"])
    return (
        f"📊 猫超月利润简报\n"
        f"📅 {month_date.year}年{month_date.month}月\n"
        f"🏪 {store}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"💰 {metric}  ¥{profit:,.2f}"
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
        raise RuntimeError(completed.stderr.strip() or "Hermes 飞书发送失败")
    raw = completed.stdout.strip().splitlines()[-1]
    result = json.loads(raw)
    if not result.get("success"):
        raise RuntimeError(str(result.get("error") or raw))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Send JST monthly profit to Feishu through Hermes.")
    parser.add_argument("--month", default=None, help="Target month in YYYY-MM. Defaults to previous month.")
    parser.add_argument("--target", default=os.getenv("MONTHLY_PROFIT_FEISHU_TARGET", DEFAULT_FEISHU_TARGET))
    parser.add_argument("--no-send", action="store_true", help="Only query and print the message.")
    args = parser.parse_args()

    month = args.month or previous_month()
    try:
        payload = run_profit_query(month)
        message = format_message(payload)
        send_result = None if args.no_send else send_feishu(message, target=args.target)
        print(
            json.dumps(
                {"success": True, "month": month, "message": message, "send_result": send_result},
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        failure_message = "\n".join(
            [
                "猫超月利润自动推送失败",
                f"月份：{month}",
                f"错误：{exc}",
                f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            ]
        )
        if not args.no_send:
            try:
                send_feishu(failure_message, target=ERROR_FEISHU_TARGET)
            except Exception as send_exc:
                print(
                    json.dumps(
                        {"success": False, "month": month, "error": str(exc), "send_error": str(send_exc)},
                        ensure_ascii=False,
                    )
                )
                return 1
        print(json.dumps({"success": False, "month": month, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
