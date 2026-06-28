"""NotchFlow 文件 inbox reporter 测试：App 活性门控 + dry-run 跳过 + 异常吞掉 + 原子落盘。"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import pytest

from core.notchflow_reporter import NotchFlowReporter


def _reporter(tmp_path: Path) -> NotchFlowReporter:
    return NotchFlowReporter(base_dir=tmp_path / "NotchFlow")


def _hold_lock(tmp_path: Path) -> int:
    """模拟 App 运行：在锁文件上持有 flock 独占锁（返回 fd，测试结束时关闭释放）。"""
    base = tmp_path / "NotchFlow"
    base.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(base / "runtime.lock"), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _inbox_files(tmp_path: Path) -> list[Path]:
    inbox = tmp_path / "NotchFlow" / "inbox"
    return sorted(inbox.glob("*.json")) if inbox.exists() else []


def test_no_write_when_app_not_running(tmp_path):
    reporter = _reporter(tmp_path)
    reporter.start("demo", "演示任务")
    # App 没在跑：不创建目录、不写文件。
    assert not (tmp_path / "NotchFlow" / "inbox").exists()
    assert _inbox_files(tmp_path) == []


def test_dry_run_never_writes_even_if_app_running(tmp_path):
    fd = _hold_lock(tmp_path)
    try:
        reporter = _reporter(tmp_path)
        reporter.start("demo", "演示任务", dry_run=True)
        reporter.success("demo", "演示任务", dry_run=True)
        assert _inbox_files(tmp_path) == []
    finally:
        os.close(fd)


def test_writes_event_when_app_running(tmp_path):
    fd = _hold_lock(tmp_path)
    try:
        reporter = _reporter(tmp_path)
        reporter.start("orders", "订单同步", "读取订单")
        files = _inbox_files(tmp_path)
        assert len(files) == 1
        event = json.loads(files[0].read_text(encoding="utf-8"))
        assert event["workflowId"] == "orders"
        assert event["taskName"] == "订单同步"
        assert event["message"] == "读取订单"
        assert event["status"] == "running"
        assert event["updatedAt"].endswith("Z")
        assert "progress" not in event
    finally:
        os.close(fd)


def test_status_mapping_and_progress_clamped(tmp_path):
    fd = _hold_lock(tmp_path)
    try:
        reporter = _reporter(tmp_path)
        reporter.success("orders", "订单同步")
        reporter.failed("orders", "订单同步")
        reporter.waiting("orders", "订单同步")
        reporter.step("orders", "订单同步", "进度", progress=5)  # 超界应被夹到 1
        events = [json.loads(p.read_text(encoding="utf-8")) for p in _inbox_files(tmp_path)]
        statuses = [e["status"] for e in events]
        assert statuses == ["success", "failed", "waiting", "running"]
        assert events[0]["progress"] == 1.0
        assert events[3]["progress"] == 1.0
    finally:
        os.close(fd)


def test_prune_keeps_only_recent(tmp_path):
    fd = _hold_lock(tmp_path)
    try:
        reporter = _reporter(tmp_path)
        for i in range(10):
            reporter.step("orders", "订单同步", f"step {i}")
        reporter._prune(keep=3)
        assert len(_inbox_files(tmp_path)) == 3
    finally:
        os.close(fd)


def test_exceptions_are_swallowed(tmp_path, monkeypatch):
    fd = _hold_lock(tmp_path)
    try:
        reporter = _reporter(tmp_path)

        def boom(_event):
            raise OSError("disk full")

        monkeypatch.setattr(reporter, "_write_atomic", boom)
        # 不应抛出——NotchFlow 不能影响 workflow。
        reporter.start("orders", "订单同步")
    finally:
        os.close(fd)
