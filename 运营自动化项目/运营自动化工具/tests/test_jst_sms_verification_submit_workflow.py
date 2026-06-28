"""jst_sms_verification_submit workflow 测试。

全程 mock 平台调用（_ops_detect/_ops_submit）与触发 workflow，验证：
- 注册 / 中文入口解析；
- INVALID_CODE / EXECUTE_REQUIRED / dry-run 不提交；
- outputs 与落盘 run.json 不含验证码明文，masked 正确；
- max_trigger_attempts 上限；TRIGGER_WORKFLOW_NOT_FOUND；触发循环按次数调用；
- 检测到 sms_required 后停止继续触发。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow

from workflows.jst_sms_verification_submit import steps
from workflows.jst_sms_verification_submit.workflow import build_workflow


@dataclass
class _FakeOpsResult:
    success: bool
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def error_code(self):
        return self.data.get("error_code")


def _run(monkeypatch, tmp_path: Path, args: list[str], *, dry_run: bool):
    runner = WorkflowRunner(tmp_path)
    return runner.run(build_workflow(), inputs={"dry_run": dry_run, "args": args}, dry_run=dry_run)


def _outputs(run):
    return run.outputs


# --- 注册 / 解析 ---

def test_workflow_registers() -> None:
    wf = discover_workflow("jst_sms_verification_submit")
    assert wf.id == "jst_sms_verification_submit"
    assert [s.id for s in wf.steps][0] == "check_inputs"


def test_chinese_entry_resolves() -> None:
    from core.task_registry import resolve_task

    assert resolve_task("聚水潭短信验证码提交") == "jst_sms_verification_submit"
    assert resolve_task("JST验证码提交") == "jst_sms_verification_submit"


# --- 校验 ---

def test_invalid_code(monkeypatch, tmp_path) -> None:
    run = _run(monkeypatch, tmp_path, ["--code", "12", "--execute"], dry_run=False)
    assert run.status == "failed"
    assert _outputs(run).get("error_code") == "INVALID_CODE"


def test_execute_required_when_not_dry_run(monkeypatch, tmp_path) -> None:
    # 不传 execute、非 dry-run → EXECUTE_REQUIRED，且绝不调用平台。
    called = {"detect": 0, "submit": 0}
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: called.__setitem__("detect", called["detect"] + 1) or _FakeOpsResult(True, {"sms_required": True}))
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: called.__setitem__("submit", called["submit"] + 1) or _FakeOpsResult(True, {}))

    run = _run(monkeypatch, tmp_path, ["--code", "1234"], dry_run=False)
    assert run.status == "failed"
    assert _outputs(run).get("error_code") == "EXECUTE_REQUIRED"
    assert called["submit"] == 0


def test_max_trigger_attempts_over_limit(monkeypatch, tmp_path) -> None:
    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--execute", "--max-trigger-attempts", "6"], dry_run=False)
    assert run.status == "failed"
    assert _outputs(run).get("error_code") == "INVALID_MAX_TRIGGER_ATTEMPTS"


# --- dry-run ---

def test_dry_run_detects_only_no_submit(monkeypatch, tmp_path) -> None:
    submit_called = {"n": 0}
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(True, {"sms_required": True, "matched_signals": ["短信验证码"], "source": "9222_chrome"}))
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: submit_called.__setitem__("n", submit_called["n"] + 1) or _FakeOpsResult(True, {}))

    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--dry-run"], dry_run=True)
    assert run.status == "dry_run_success"
    assert submit_called["n"] == 0
    out = _outputs(run)
    assert out["submitted"] is False
    assert out["masked_code"] == "****"


def test_dry_run_browser_not_running_is_soft(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(False, {"error_code": "BROWSER_NOT_RUNNING"}))
    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--dry-run"], dry_run=True)
    assert run.status == "dry_run_success"


# --- 提交成功 + 无明文 ---

def test_submit_success_no_plaintext(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(True, {"sms_required": True, "matched_signals": ["短信验证码"]}))
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: _FakeOpsResult(True, {"submitted": True, "verified": True, "masked_code": "****", "source": "9222_chrome"}))

    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--execute"], dry_run=False)
    assert run.status == "success"
    out = _outputs(run)
    assert out["submitted"] is True and out["verified"] is True
    assert out["masked_code"] == "****"

    # 落盘 run.json 不得含明文验证码（验证 runtime 脱敏 + outputs 无明文）。
    run_json = (Path(runner_dir(run, tmp_path)) / "run.json").read_text(encoding="utf-8")
    assert "1234" not in run_json


def runner_dir(run, tmp_path: Path) -> Path:
    # run 落在 tmp_path/<YYYY-MM?>/run_xxx —— 直接找含 run.json 的目录
    for p in Path(tmp_path).rglob("run.json"):
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("run_id") == run.run_id:
            return p.parent
    raise AssertionError("run.json not found")


# --- 触发逻辑 ---

def test_trigger_workflow_not_found(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(True, {"sms_required": False}))

    def _boom(_id):
        raise SystemExit("unknown")

    monkeypatch.setattr(steps, "discover_workflow", _boom)
    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--execute", "--trigger-with-pickup-watch"], dry_run=False)
    assert run.status == "failed"
    assert _outputs(run).get("error_code") == "TRIGGER_WORKFLOW_NOT_FOUND"


def test_trigger_loop_calls_until_dialog(monkeypatch, tmp_path) -> None:
    # find_trigger_workflow 用 discover_workflow → 放行
    monkeypatch.setattr(steps, "discover_workflow", lambda _id: object())
    monkeypatch.setattr(steps.time, "sleep", lambda _s: None)

    trigger_calls = {"n": 0}

    def _run_trigger(workflow_id, args=None):
        trigger_calls["n"] += 1
        return "success"

    monkeypatch.setattr(steps, "run_trigger_workflow", _run_trigger)

    # detect 序列：初次 False（无弹窗）→ 触发后第 1 次 False → 第 2 次 True（出现）
    detect_seq = iter([
        _FakeOpsResult(True, {"sms_required": False}),  # detect_sms_dialog step
        _FakeOpsResult(True, {"sms_required": False}),  # 触发第 1 次后复检
        _FakeOpsResult(True, {"sms_required": True}),   # 触发第 2 次后复检 → 命中
    ])
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: next(detect_seq))
    submitted = {"n": 0}
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: submitted.__setitem__("n", submitted["n"] + 1) or _FakeOpsResult(True, {"submitted": True, "verified": True, "masked_code": "****"}))

    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--execute", "--trigger-with-pickup-watch", "--max-trigger-attempts", "3", "--trigger-cooldown-seconds", "0"], dry_run=False)
    assert run.status == "success", run.errors
    assert trigger_calls["n"] == 2  # 第 2 次触发后命中即停
    assert submitted["n"] == 1


def test_trigger_attempts_exceeded(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(steps, "discover_workflow", lambda _id: object())
    monkeypatch.setattr(steps, "run_trigger_workflow", lambda workflow_id, args=None: "success")
    monkeypatch.setattr(steps.time, "sleep", lambda _s: None)
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(True, {"sms_required": False}))
    submitted = {"n": 0}
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: submitted.__setitem__("n", submitted["n"] + 1) or _FakeOpsResult(True, {}))

    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--execute", "--trigger-with-pickup-watch", "--max-trigger-attempts", "3", "--trigger-cooldown-seconds", "0"], dry_run=False)
    assert run.status == "failed"
    assert _outputs(run).get("error_code") == "TRIGGER_ATTEMPTS_EXCEEDED"
    assert _outputs(run).get("trigger_attempts") == 3
    assert submitted["n"] == 0


def test_existing_dialog_skips_trigger(monkeypatch, tmp_path) -> None:
    # 已检测到弹窗时即使启用触发也不触发
    monkeypatch.setattr(steps, "discover_workflow", lambda _id: object())
    trig = {"n": 0}
    monkeypatch.setattr(steps, "run_trigger_workflow", lambda workflow_id, args=None: trig.__setitem__("n", trig["n"] + 1))
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(True, {"sms_required": True}))
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: _FakeOpsResult(True, {"submitted": True, "verified": True, "masked_code": "****"}))

    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--execute", "--trigger-with-pickup-watch"], dry_run=False)
    assert run.status == "success"
    assert trig["n"] == 0


def test_no_dialog_no_trigger_reports_not_found(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(True, {"sms_required": False}))
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: _FakeOpsResult(True, {}))
    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--execute"], dry_run=False)
    assert run.status == "failed"
    assert _outputs(run).get("error_code") == "SMS_DIALOG_NOT_FOUND"


# --- 通用化：--challenge-file 用原 workflow 触发（不再写死 jst_pickup_watch）---

def test_challenge_file_triggers_original_workflow(monkeypatch, tmp_path) -> None:
    import json as _json

    challenge_path = tmp_path / "jst_sms_pending.json"
    challenge_path.write_text(
        _json.dumps(
            {
                "challenge_id": "jst_sms_x",
                "workflow_id": "jst_tmcs_shop_product_sales_analysis",
                "workflow_name": "聚水潭商品利润宝贝分析",
                "args": ["--days", "7", "--execute"],
                "resume_command": "python3 run.py workflow jst_tmcs_shop_product_sales_analysis --days 7 --execute",
                "status": "waiting_code",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(steps, "discover_workflow", lambda _id: object())
    triggered = {"workflow_id": None, "args": None, "n": 0}

    def _run_trigger(workflow_id, args=None):
        triggered["workflow_id"] = workflow_id
        triggered["args"] = list(args or [])
        triggered["n"] += 1
        return "success"

    monkeypatch.setattr(steps, "run_trigger_workflow", _run_trigger)
    monkeypatch.setattr(steps.time, "sleep", lambda _s: None)

    detect_seq = iter([
        _FakeOpsResult(True, {"sms_required": False}),  # 初次无弹窗
        _FakeOpsResult(True, {"sms_required": True}),   # 触发后命中
    ])
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: next(detect_seq))
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: _FakeOpsResult(True, {"submitted": True, "verified": True, "masked_code": "****"}))

    run = _run(
        monkeypatch,
        tmp_path,
        ["--code", "1234", "--challenge-file", str(challenge_path), "--execute", "--trigger-cooldown-seconds", "0"],
        dry_run=False,
    )
    assert run.status == "success", run.errors
    # 关键：用 challenge 里记录的原 workflow 触发，而非 jst_pickup_watch
    assert triggered["workflow_id"] == "jst_tmcs_shop_product_sales_analysis"
    assert triggered["args"] == ["--days", "7", "--execute"]
    assert _outputs(run).get("resume_command") == "python3 run.py workflow jst_tmcs_shop_product_sales_analysis --days 7 --execute"
    # 验证通过后 challenge 标记 verified
    updated = _json.loads(challenge_path.read_text(encoding="utf-8"))
    assert updated["status"] == "verified"


def test_challenge_file_unknown_workflow_reports_not_found(monkeypatch, tmp_path) -> None:
    import json as _json

    challenge_path = tmp_path / "jst_sms_pending.json"
    challenge_path.write_text(
        _json.dumps({"workflow_id": "no_such_workflow", "args": []}), encoding="utf-8"
    )

    def _boom(_id):
        raise SystemExit("unknown")

    monkeypatch.setattr(steps, "discover_workflow", _boom)
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(True, {"sms_required": False}))
    run = _run(monkeypatch, tmp_path, ["--code", "1234", "--challenge-file", str(challenge_path), "--execute"], dry_run=False)
    assert run.status == "failed"
    assert _outputs(run).get("error_code") == "TRIGGER_WORKFLOW_NOT_FOUND"


def test_six_digit_code_accepted(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(steps, "_ops_detect", lambda **k: _FakeOpsResult(True, {"sms_required": True}))
    monkeypatch.setattr(steps, "_ops_submit", lambda **k: _FakeOpsResult(True, {"submitted": True, "verified": True, "masked_code": "******"}))
    run = _run(monkeypatch, tmp_path, ["--code", "123456", "--execute"], dry_run=False)
    assert run.status == "success", run.errors
    assert _outputs(run).get("masked_code") == "******"
    # run.json 不含明文
    blob = json.dumps(run.to_dict(), ensure_ascii=False)
    assert "123456" not in blob
