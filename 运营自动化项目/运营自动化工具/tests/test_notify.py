from __future__ import annotations

from core.runtime import send_notification


def test_dry_run_never_sends() -> None:
    calls: list = []
    result = send_notification("内容", dry_run=True, sender=lambda c, msgtype="text": calls.append(c))
    assert calls == []
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert result["preview"] == "内容"


def test_empty_content_not_sent() -> None:
    calls: list = []
    result = send_notification("", dry_run=False, sender=lambda c, msgtype="text": calls.append(c))
    assert calls == []
    assert result["sent"] is False


def test_real_send_uses_sender_and_msgtype() -> None:
    calls: list = []

    def fake(content, msgtype="text"):
        calls.append((content, msgtype))
        return {"success": True, "sent": True}

    result = send_notification("告警", dry_run=False, msgtype="markdown", sender=fake)
    assert calls == [("告警", "markdown")]
    assert result["sent"] is True


def test_real_send_passes_key_when_sender_supports_it() -> None:
    calls: list = []

    def fake(content, msgtype="text", key=None):
        calls.append((content, msgtype, key))
        return {"success": True, "sent": True}

    result = send_notification("告警", dry_run=False, msgtype="markdown", key="猫超售后", sender=fake)
    assert calls == [("告警", "markdown", "猫超售后")]
    assert result["sent"] is True


def test_real_send_passes_target_when_sender_supports_it() -> None:
    calls: list = []

    def fake(content, msgtype="text", target=None):
        calls.append((content, msgtype, target))
        return {"success": True, "sent": True, "target": target}

    result = send_notification("告警", dry_run=False, msgtype="markdown", target="feishu", sender=fake)
    assert calls == [("告警", "markdown", "feishu")]
    assert result["sent"] is True
    assert result["target"] == "feishu"


def test_dry_run_keeps_target_metadata() -> None:
    result = send_notification("内容", dry_run=True, target="feishu:oc_xxx")
    assert result["sent"] is False
    assert result["dry_run"] is True
    assert result["target"] == "feishu:oc_xxx"
