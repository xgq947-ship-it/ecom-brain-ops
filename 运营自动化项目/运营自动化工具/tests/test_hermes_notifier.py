from __future__ import annotations

from notifier.hermes import HermesNotifier


def test_send_wecom_with_key_routes_to_wecom(monkeypatch) -> None:
    notifier = HermesNotifier(enabled=True, default_feishu_target="feishu:demo")

    monkeypatch.setattr(notifier, "_validate_runtime", lambda: None)
    monkeypatch.setattr(notifier, "_send_wecom", lambda message, *, msgtype, key: {"ok": True, "key": key, "msgtype": msgtype})

    result = notifier.send("告警", msgtype="markdown", key="猫超售后")
    assert result["sent"] is True
    assert result["target"] == "wecom"
    assert result["key"] == "猫超售后"
    assert result["result"]["key"] == "猫超售后"


def test_send_feishu_routes_to_send_message_tool(monkeypatch) -> None:
    notifier = HermesNotifier(enabled=True, default_feishu_target="feishu:demo")

    monkeypatch.setattr(notifier, "_validate_runtime", lambda: None)
    monkeypatch.setattr(notifier, "_send_message_tool", lambda target, message: {"success": True, "target": target, "message": message})

    result = notifier.send("月报", target="feishu")
    assert result["sent"] is True
    assert result["target"] == "feishu"
    assert result["result"]["target"] == "feishu:demo"


def test_send_weixin_routes_to_send_message_tool(monkeypatch) -> None:
    notifier = HermesNotifier(enabled=True, weixin_target="weixin")

    monkeypatch.setattr(notifier, "_validate_runtime", lambda: None)
    monkeypatch.setattr(notifier, "_send_message_tool", lambda target, message: {"success": True, "target": target, "message": message})

    result = notifier.send("日报", target="weixin")
    assert result["sent"] is True
    assert result["target"] == "weixin"
    assert result["result"]["target"] == "weixin"


def test_dry_run_preserves_target_and_key() -> None:
    notifier = HermesNotifier(enabled=True)

    result = notifier.send("预览", dry_run=True, target="wecom:猫超售后")
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert result["target"] == "wecom"
    assert result["key"] == "猫超售后"
