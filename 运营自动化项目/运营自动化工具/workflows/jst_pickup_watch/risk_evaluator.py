"""聚水潭揽收监控的纯业务逻辑（风险评估 + 提醒文案）。

本模块只做内存计算：读取业务配置、根据付款时间评估揽收风险等级、生成提醒文案。
不请求平台、不发送通知、不依赖 Ops-Cli —— 平台读取由 steps.py 经
clients/ops_cli_client.py 完成，通知由 steps.py 经 core.runtime.notify 完成。
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from core.config_loader import get_path

ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or get_path("pickup_watch_config")
    if not Path(config_path).is_absolute():
        config_path = ROOT / config_path
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def _parse_datetime(value: str, now: datetime) -> datetime:
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=now.tzinfo)


def _is_maochao(order: dict[str, Any]) -> bool:
    values = " ".join(str(order.get(key) or "") for key in ("platform", "order_source", "shop_name")).lower()
    return any(token in values for token in ("猫超", "天猫超市", "cat_supermarket", "tmcs"))


def evaluate_order(order: dict[str, Any], config: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    rule = config["platform_rules"]["cat_supermarket"]
    jst_pay_time = _parse_datetime(str(order["jst_pay_time"]), now)
    real_pay_value = str(order.get("maochao_real_pay_time") or "").strip()
    offset_minutes = 0
    if real_pay_value and _is_maochao(order):
        effective_pay_time = _parse_datetime(real_pay_value, now)
        pay_time_source = "maochao_real_pay_time"
    elif rule.get("enabled", True) and _is_maochao(order):
        offset_minutes = int(rule["pay_time_offset_minutes"])
        effective_pay_time = jst_pay_time - timedelta(minutes=offset_minutes)
        pay_time_source = "jst_pay_time_adjusted"
    else:
        effective_pay_time = jst_pay_time
        pay_time_source = "jst_pay_time"

    risk_hours = round((now - effective_pay_time).total_seconds() / 3600, 2)
    stop_time = time.fromisoformat(config["pickup_watch"]["warehouse"]["stop_shipping_time"])
    after_stop = effective_pay_time.time() >= stop_time
    suppress = bool(
        rule.get("after_1730_orders_next_day", True)
        and after_stop
        and effective_pay_time.date() == now.date()
    )
    thresholds = config["pickup_watch"]["risk_thresholds"]
    if order.get("has_pickup_record") or suppress:
        risk_level = "正常"
    elif risk_hours >= float(thresholds["timeout_hours"]):
        risk_level = "已超时"
    elif risk_hours >= float(thresholds["high_risk_hours"]):
        risk_level = "高危提醒"
    elif risk_hours >= float(thresholds["normal_reminder_hours"]):
        risk_level = "普通提醒"
    else:
        risk_level = "正常"

    return {
        **order,
        "effective_pay_time": effective_pay_time.isoformat(timespec="seconds"),
        "pay_time_source": pay_time_source,
        "pay_time_offset_minutes": offset_minutes,
        "check_time": now.isoformat(timespec="seconds"),
        "risk_hours": risk_hours,
        "risk_level": risk_level,
        "after_1730_order": after_stop,
        "suppressed_until_next_day": suppress,
    }


def evaluate_orders(orders: list[dict[str, Any]], config: dict[str, Any], *, now: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    evaluated = [evaluate_order(order, config, now=now) for order in orders]
    abnormal = [item for item in evaluated if not item.get("has_pickup_record") and item["risk_level"] != "正常"]
    abnormal.sort(key=lambda item: float(item["risk_hours"]), reverse=True)
    counts = {
        "checked_orders": len(evaluated),
        "abnormal_orders": len(abnormal),
        "normal_reminder": sum(item["risk_level"] == "普通提醒" for item in abnormal),
        "high_risk": sum(item["risk_level"] == "高危提醒" for item in abnormal),
        "timed_out": sum(item["risk_level"] == "已超时" for item in abnormal),
        "suppressed_after_1730": sum(bool(item["suppressed_until_next_day"]) for item in evaluated),
    }
    return abnormal, counts


def build_notification_content(*, counts: dict[str, int], rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "无异常订单"
    checked_at = ""
    if rows:
        try:
            checked_at = _parse_datetime(str(rows[0]["check_time"]), datetime.now().astimezone()).strftime("%m-%d %H:%M")
        except (KeyError, TypeError, ValueError):
            checked_at = ""
    lines = [f"揽收异常 {counts['abnormal_orders']}单"]
    if checked_at:
        lines.append(f"检查：{checked_at}")
    for level, label in (("已超时", "已超时"), ("高危提醒", "高危"), ("普通提醒", "普通提醒")):
        order_lines = [
            _format_notification_order(index, item)
            for index, item in enumerate((row for row in rows if row["risk_level"] == level), start=1)
        ]
        order_lines = [line for line in order_lines if line]
        if order_lines:
            lines.extend(["", f"{label}：", *order_lines])
    return "\n".join(lines)


def _format_notification_order(index: int, item: dict[str, Any]) -> str:
    order_no = str(item.get("platform_order_no") or item.get("jst_order_no") or "").strip()
    if not order_no:
        return ""
    try:
        risk_hours = float(item["risk_hours"])
    except (KeyError, TypeError, ValueError):
        return f"{index}. {order_no}"
    if item.get("risk_level") == "已超时":
        overdue_hours = max(0.0, risk_hours - 24)
        return f"{index}. {order_no}  距付{risk_hours:.1f}h  超{overdue_hours:.1f}h"
    if item.get("risk_level") == "高危提醒":
        remaining_hours = max(0.0, 24 - risk_hours)
        return f"{index}. {order_no}  距付{risk_hours:.1f}h  剩{remaining_hours:.1f}h超时"
    return f"{index}. {order_no}  距付{risk_hours:.1f}h"
