from __future__ import annotations

from datetime import datetime
import sys

from tasks import jst_pickup_watch
from workflows.jst_pickup_watch import risk_evaluator


def config() -> dict:
    return {
        "pickup_watch": {
            "hours": 48,
            "risk_thresholds": {
                "normal_reminder_hours": 12,
                "high_risk_hours": 20,
                "timeout_hours": 24,
            },
            "warehouse": {"stop_shipping_time": "17:30"},
        },
        "platform_rules": {
            "cat_supermarket": {
                "enabled": True,
                "pay_time_offset_minutes": 30,
                "after_1730_orders_next_day": True,
            }
        },
    }


def test_maochao_order_uses_adjusted_effective_pay_time() -> None:
    order = {
        "platform": "天猫超市",
        "jst_pay_time": "2026-05-26T22:30:00+08:00",
        "has_pickup_record": False,
    }

    evaluated = risk_evaluator.evaluate_order(
        order,
        config(),
        now=datetime.fromisoformat("2026-05-27T18:00:00+08:00"),
    )

    assert evaluated["effective_pay_time"] == "2026-05-26T22:00:00+08:00"
    assert evaluated["pay_time_source"] == "jst_pay_time_adjusted"
    assert evaluated["pay_time_offset_minutes"] == 30
    assert evaluated["risk_level"] == "高危提醒"


def test_real_maochao_pay_time_wins_over_adjustment() -> None:
    order = {
        "platform": "猫超",
        "jst_pay_time": "2026-05-26T22:30:00+08:00",
        "maochao_real_pay_time": "2026-05-26T21:55:00+08:00",
        "has_pickup_record": False,
    }

    evaluated = risk_evaluator.evaluate_order(
        order,
        config(),
        now=datetime.fromisoformat("2026-05-27T18:00:00+08:00"),
    )

    assert evaluated["effective_pay_time"] == "2026-05-26T21:55:00+08:00"
    assert evaluated["pay_time_source"] == "maochao_real_pay_time"
    assert evaluated["risk_level"] == "高危提醒"


def test_after_1730_effective_pay_time_is_not_alerted_on_same_evening() -> None:
    order = {
        "platform": "其他平台",
        "jst_pay_time": "2026-05-27T17:31:00+08:00",
        "has_pickup_record": False,
    }

    evaluated = risk_evaluator.evaluate_order(
        order,
        config(),
        now=datetime.fromisoformat("2026-05-27T18:00:00+08:00"),
    )

    assert evaluated["after_1730_order"] is True
    assert evaluated["suppressed_until_next_day"] is True
    assert evaluated["risk_level"] == "正常"


def test_notification_only_lists_abnormal_platform_order_numbers() -> None:
    rows = [
        {
            "platform_order_no": "P-TIMEOUT",
            "risk_level": "已超时",
            "risk_hours": 32.34,
            "check_time": "2026-05-28T17:30:00+08:00",
        },
        {
            "platform_order_no": "P-HIGH",
            "risk_level": "高危提醒",
            "risk_hours": 21.25,
            "check_time": "2026-05-28T17:30:00+08:00",
        },
        {
            "platform_order_no": "P-REMIND",
            "risk_level": "普通提醒",
            "risk_hours": 13.5,
            "check_time": "2026-05-28T17:30:00+08:00",
        },
    ]

    content = risk_evaluator.build_notification_content(
        counts={"abnormal_orders": 3, "normal_reminder": 1, "high_risk": 1, "timed_out": 1},
        rows=rows,
    )

    assert content == (
        "揽收异常 3单\n"
        "检查：05-28 17:30\n"
        "\n"
        "已超时：\n"
        "1. P-TIMEOUT  距付32.3h  超8.3h\n"
        "\n"
        "高危：\n"
        "1. P-HIGH  距付21.2h  剩2.8h超时\n"
        "\n"
        "普通提醒：\n"
        "1. P-REMIND  距付13.5h"
    )
    assert not hasattr(risk_evaluator, "write_reports")


def test_main_routes_to_workflow_without_direct_platform_or_notification(monkeypatch) -> None:
    # 薄 wrapper 只透传参数给 workflow：结构上不 import 平台/通知能力，故无从直连。
    calls: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["jst_pickup_watch.py", "--hours", "48", "--dry-run"])
    monkeypatch.setattr(jst_pickup_watch, "_run_workflow", lambda args: calls.append(list(args)) or 0, raising=False)

    assert jst_pickup_watch.main() == 0
    assert calls == [["jst_pickup_watch", "--hours", "48", "--dry-run"]]
    assert not hasattr(jst_pickup_watch, "run_ops_json")
    assert not hasattr(jst_pickup_watch, "send_wecom")
