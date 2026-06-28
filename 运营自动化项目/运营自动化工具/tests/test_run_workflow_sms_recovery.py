"""run.py 通用 SMS 中断恢复接线测试。

验证：
- 任意 JST workflow 因短信验证失败 → 自动登记 challenge（记录原 workflow_id + args）。
- 已有 active challenge 时，新的 JST workflow 不再执行（单并发闸门），返回 0。
- 非 SMS 失败不登记 challenge。
"""
from __future__ import annotations

import core.runtime.registry as registry
import run as run_mod
from clients import jst_sms_challenge as ch
from core.runtime import build_workflow, failure_result, step, success_result


class _Ctx:
    """替身 TaskContext，避免写真实 runtime/context。"""

    def __init__(self, *a, **k):
        pass

    def add_input(self, *a, **k):
        pass

    def add_output(self, *a, **k):
        pass

    def add_artifact(self, *a, **k):
        pass

    def add_error(self, *a, **k):
        pass

    def finish(self, *a, **k):
        return "/tmp/_ctx.json"


def _wire(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "PENDING_JSON", tmp_path / "pending.json")
    monkeypatch.setattr(ch, "PENDING_LEGACY", tmp_path / "pending")
    monkeypatch.setattr(run_mod, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(run_mod, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(run_mod, "TaskContext", _Ctx)
    monkeypatch.setattr(run_mod, "_detect_phone_mask", lambda: "156****388")


def _sms_fail_step(ctx):
    return failure_result(
        ["OpsCommandError [AUTH_SMS_REQUIRED]：查询轨迹需要完成短信验证。"],
        outputs={"error_code": "AUTH_SMS_REQUIRED"},
    )


def test_sms_failure_registers_challenge_with_original_workflow(monkeypatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path)
    wf = build_workflow("jst_pickup_watch", "聚水潭揽收监控", [step("fetch", "拉取", _sms_fail_step)])
    monkeypatch.setattr(registry, "discover_workflow", lambda _id: wf)

    rc = run_mod.run_workflow(["jst_pickup_watch", "--notify"])
    assert rc == 1  # workflow 仍是失败

    challenge = ch.read_challenge()
    assert challenge is not None
    assert challenge["workflow_id"] == "jst_pickup_watch"
    assert challenge["args"] == ["--notify"]
    assert challenge["phone_mask"] == "156****388"
    assert challenge["resume_command"] == "python3 run.py workflow jst_pickup_watch --notify"
    assert challenge["status"] == ch.STATUS_WAITING


def test_single_concurrency_blocks_second_jst_workflow(monkeypatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path)
    # 先放一个 active challenge
    ch.create_challenge(workflow_id="jst_pickup_watch", workflow_name="聚水潭揽收监控", args=["--notify"])

    ran = {"n": 0}

    def _ok_step(ctx):
        ran["n"] += 1
        return success_result(outputs={})

    wf = build_workflow("jst_tmcs_shop_product_sales_analysis", "聚水潭商品利润宝贝分析", [step("go", "执行", _ok_step)])
    monkeypatch.setattr(registry, "discover_workflow", lambda _id: wf)

    rc = run_mod.run_workflow(["jst_tmcs_shop_product_sales_analysis", "--days", "7", "--execute"])
    assert rc == 0  # 闸门：直接返回，不算失败
    assert ran["n"] == 0  # workflow 未真正执行


def test_non_sms_failure_does_not_register_challenge(monkeypatch, tmp_path) -> None:
    _wire(monkeypatch, tmp_path)

    def _other_fail(ctx):
        return failure_result(["PLATFORM_REQUEST_FAILED：网络超时"], outputs={"error_code": "PLATFORM_REQUEST_FAILED"})

    wf = build_workflow("jst_pickup_watch", "聚水潭揽收监控", [step("fetch", "拉取", _other_fail)])
    monkeypatch.setattr(registry, "discover_workflow", lambda _id: wf)

    rc = run_mod.run_workflow(["jst_pickup_watch", "--notify"])
    assert rc == 1
    assert ch.read_challenge() is None
