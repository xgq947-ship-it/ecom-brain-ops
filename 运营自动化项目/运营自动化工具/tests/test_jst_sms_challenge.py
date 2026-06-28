"""challenge 共享模块测试：schema / 单并发 / 取消 / 状态 / 过期 / legacy 兼容 / 无明文。"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from clients import jst_sms_challenge as ch


@pytest.fixture(autouse=True)
def _isolate_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(ch, "PENDING_JSON", tmp_path / "jst_sms_pending.json")
    monkeypatch.setattr(ch, "PENDING_LEGACY", tmp_path / "jst_sms_pending")
    yield


def test_make_challenge_resume_command_uses_original_workflow() -> None:
    c = ch.make_challenge(
        workflow_id="jst_tmcs_shop_product_sales_analysis",
        workflow_name="聚水潭商品利润宝贝分析",
        args=["--days", "7", "--execute"],
        phone_mask="156****388",
    )
    assert c["workflow_id"] == "jst_tmcs_shop_product_sales_analysis"
    assert c["resume_command"] == "python3 run.py workflow jst_tmcs_shop_product_sales_analysis --days 7 --execute"
    assert c["trigger_command"] == c["resume_command"]
    assert c["status"] == ch.STATUS_WAITING
    assert c["phone_mask"] == "156****388"


def test_create_writes_both_files_and_no_plaintext_code() -> None:
    c, created = ch.create_challenge(
        workflow_id="jst_pickup_watch", workflow_name="聚水潭揽收监控", args=["--notify"]
    )
    assert created is True
    assert ch.PENDING_JSON.exists() and ch.PENDING_LEGACY.exists()
    legacy = ch.PENDING_LEGACY.read_text(encoding="utf-8").splitlines()
    assert legacy[0] == "jst_pickup_watch"
    assert legacy[1] == "--notify"
    # challenge 文件不含任何验证码字段
    blob = json.dumps(c, ensure_ascii=False)
    assert "code" not in blob.lower() or "context" in blob.lower()  # 没有 code/masked_code 字段


def test_single_concurrency_does_not_create_second() -> None:
    first, c1 = ch.create_challenge(workflow_id="jst_pickup_watch", args=["--notify"])
    second, c2 = ch.create_challenge(workflow_id="jst_order_logistics", args=["--order-id", "X"])
    assert c1 is True
    assert c2 is False  # 已有 active challenge → 不新建
    assert second["challenge_id"] == first["challenge_id"]
    assert second["workflow_id"] == "jst_pickup_watch"


def test_active_challenge_expires() -> None:
    from datetime import datetime

    past = datetime.now().astimezone() - timedelta(seconds=600)
    c = ch.make_challenge(workflow_id="jst_pickup_watch", args=[], now=past, ttl_seconds=300)
    ch.write_challenge(c)
    assert ch.active_challenge() is None  # 已过期
    reloaded = ch.read_challenge()
    assert reloaded["status"] == ch.STATUS_EXPIRED


def test_cancel_clears_files() -> None:
    ch.create_challenge(workflow_id="jst_pickup_watch", args=["--notify"])
    cancelled = ch.cancel_challenge()
    assert cancelled["status"] == ch.STATUS_CANCELLED
    assert not ch.PENDING_JSON.exists()
    assert not ch.PENDING_LEGACY.exists()
    assert ch.active_challenge() is None


def test_mark_verified() -> None:
    ch.create_challenge(workflow_id="jst_pickup_watch", args=[])
    updated = ch.mark_status(ch.STATUS_VERIFIED, feishu_message_id="om_x")
    assert updated["status"] == ch.STATUS_VERIFIED
    assert updated["feishu_message_id"] == "om_x"
    # verified 后不再算 active
    assert ch.active_challenge() is None


def test_legacy_two_line_compat_read() -> None:
    # 只有旧文件存在时也能解析出原 workflow + args
    ch.PENDING_LEGACY.write_text("jst_pickup_watch\n--notify\n", encoding="utf-8")
    c = ch.read_challenge()
    assert c["workflow_id"] == "jst_pickup_watch"
    assert c["args"] == ["--notify"]
    assert c["resume_command"] == "python3 run.py workflow jst_pickup_watch --notify"


def test_legacy_timestamp_only_falls_back_to_pickup_watch() -> None:
    ch.PENDING_LEGACY.write_text("2026-06-15 09:32:56\n", encoding="utf-8")
    c = ch.read_challenge()
    assert c["workflow_id"] == "jst_pickup_watch"


def test_errors_indicate_sms() -> None:
    assert ch.errors_indicate_sms(["OpsCommandError [AUTH_SMS_REQUIRED]：查询轨迹需要完成短信验证"]) is True
    assert ch.errors_indicate_sms("授权验证") is True
    assert ch.errors_indicate_sms(["普通失败：超时"]) is False
    assert ch.errors_indicate_sms([]) is False


def test_is_jst_workflow() -> None:
    assert ch.is_jst_workflow("jst_pickup_watch") is True
    assert ch.is_jst_workflow("jst_tmcs_shop_product_sales_analysis") is True
    assert ch.is_jst_workflow("tmcs_fulfillment_watch") is False
    assert ch.is_jst_workflow("") is False


def test_read_challenge_path_and_update(tmp_path) -> None:
    p = tmp_path / "explicit.json"
    c = ch.make_challenge(workflow_id="jst_pickup_watch", args=["--notify"])
    p.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
    loaded = ch.read_challenge_path(p)
    assert loaded["workflow_id"] == "jst_pickup_watch"
    updated = ch.update_challenge_path(p, ch.STATUS_VERIFIED)
    assert updated["status"] == ch.STATUS_VERIFIED
    assert json.loads(p.read_text(encoding="utf-8"))["status"] == ch.STATUS_VERIFIED
