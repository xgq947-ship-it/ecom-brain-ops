# -*- coding: utf-8 -*-
"""ai_file_iterate workflow 测试（不调用真实 AI）。

覆盖 CLAUDE.md 第 8 节：注册、dry-run 跑通、中文入口、危险动作 dry-run 不执行、产物。
"""

from __future__ import annotations

from pathlib import Path

from core.runtime import WorkflowRunner
from core.runtime.registry import discover_workflow
from workflows.ai_file_iterate import engine, steps


# 1. workflow 能注册
def test_workflow_registers():
    wf = discover_workflow("ai_file_iterate")
    assert wf.id == "ai_file_iterate"
    assert [s.id for s in wf.steps] == ["check_inputs", "iterate", "collect_outputs"]


# 2. 中文 alias 解析
def test_chinese_alias():
    import sys
    sys.argv = ["run.py"]
    from run import resolve_task
    assert resolve_task("文件优化") == "ai_file_iterate"
    assert resolve_task("润色文件") == "ai_file_iterate"


# 3. parse_request 抽路径 + 标准
def test_parse_request(tmp_path):
    f = tmp_path / "稿子.md"
    f.write_text("初稿", encoding="utf-8")
    t, b = engine.parse_request(f"给我优化一下这个文件 {f}")
    assert t == str(f) and b == ""
    t, b = engine.parse_request(f"帮我改得更专业 {f}")
    assert t == str(f) and "更专业" in b
    t, b = engine.parse_request("优化 ~/不存在_xyz.md")
    assert t is None


# 4. dry-run 跑通且不调用真实 AI（monkeypatch 守门）
def test_dry_run_no_real_ai(monkeypatch):
    called = {"run_agent": False}
    monkeypatch.setattr(engine, "run_agent",
                        lambda *a, **k: called.__setitem__("run_agent", True))
    wf = discover_workflow("ai_file_iterate")
    runner = WorkflowRunner(Path("/tmp/_ai_iter_test_runs"))
    run = runner.run(wf, inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)
    assert run.status in ("success", "dry_run_success")
    assert called["run_agent"] is False  # dry-run 绝不启动 AI


# 5. 危险动作（真实迭代）在 dry-run 下不执行：iterate 返回 skipped
def test_iterate_dry_run_skipped(monkeypatch, tmp_path):
    monkeypatch.setattr(steps, "RUN_ROOT", tmp_path / "rt")
    monkeypatch.setattr(engine, "run_agent",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dry-run 不应启动 AI")))
    wf = discover_workflow("ai_file_iterate")
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(wf, inputs={"dry_run": True, "args": ["--dry-run"]}, dry_run=True)
    assert run.status in ("success", "dry_run_success")


# 6. run_iteration 端到端（假 agent），产出成品 + 收敛
def test_run_iteration_with_fake_agents(monkeypatch, tmp_path):
    target = tmp_path / "t.md"
    target.write_text("短", encoding="utf-8")

    state = {"n": 0}

    def fake_run_agent(agent_cfg, prompt, cfg, cwd, log_path, tmp_prompt):
        Path(log_path).write_text("done\n", encoding="utf-8")
        work = Path(cwd) / "work.md"
        state["n"] += 1
        if state["n"] == 1:
            work.write_text("这是被显著改写、变长很多的新版本内容。" * 4, encoding="utf-8")
        # 第 2 轮不改 -> 触发收敛

        class P:
            returncode = 0
            def poll(self): return 0
        return P()

    monkeypatch.setattr(engine, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "wait_with_timeout", lambda proc, t: True)

    cfg = engine.load_engine_config(tmp_path)
    cfg["order"] = ["claude", "codex"]
    status = engine.run_iteration(cfg, target, "改清晰", tmp_path / "rt", max_rounds=4)
    assert status["result"] == "completed"
    assert Path(status["final_file"]).exists()
    assert status["final_file"].endswith("t.final.md")
    assert target.read_text(encoding="utf-8") == "短"  # 原文件未动


# 6b. run_iteration 每轮回调 on_progress（供 NotchFlow 逐轮上报）
def test_run_iteration_emits_progress(monkeypatch, tmp_path):
    target = tmp_path / "t.md"
    target.write_text("短", encoding="utf-8")

    def fake_run_agent(agent_cfg, prompt, cfg, cwd, log_path, tmp_prompt):
        Path(log_path).write_text("done\n", encoding="utf-8")
        # 不改 work -> 立即收敛，迭代尽快结束

        class P:
            returncode = 0
            def poll(self): return 0
        return P()

    monkeypatch.setattr(engine, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "wait_with_timeout", lambda proc, t: True)

    events = []
    cfg = engine.load_engine_config(tmp_path)
    cfg["order"] = ["claude", "codex"]
    engine.run_iteration(cfg, target, "改清晰", tmp_path / "rt",
                         max_rounds=4, on_progress=events.append)

    phases = [e["phase"] for e in events]
    assert "round_start" in phases and "round_done" in phases
    first = events[0]
    assert first == {"phase": "round_start", "round": 1, "max_rounds": 4, "agent": "claude"}
    # 每个 round_start 都应配一个 round_done
    assert phases.count("round_start") == phases.count("round_done")


# 6c. on_progress 回调抛异常不影响迭代主流程
def test_run_iteration_progress_errors_swallowed(monkeypatch, tmp_path):
    target = tmp_path / "t.md"
    target.write_text("短", encoding="utf-8")

    def fake_run_agent(agent_cfg, prompt, cfg, cwd, log_path, tmp_prompt):
        Path(log_path).write_text("done\n", encoding="utf-8")

        class P:
            returncode = 0
            def poll(self): return 0
        return P()

    monkeypatch.setattr(engine, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "wait_with_timeout", lambda proc, t: True)

    def boom(event):
        raise RuntimeError("notchflow 挂了")

    cfg = engine.load_engine_config(tmp_path)
    cfg["order"] = ["claude", "codex"]
    status = engine.run_iteration(cfg, target, "改清晰", tmp_path / "rt",
                                  max_rounds=2, on_progress=boom)
    assert status["result"] == "completed"


# 6d. 自报「已收敛」标记在文件仍大改时不采信 -> 不提前停（修复"2 轮即停"）
def test_marker_ignored_when_big_change(monkeypatch, tmp_path):
    target = tmp_path / "t.md"
    target.write_text("起始内容", encoding="utf-8")

    state = {"n": 0}

    def fake_run_agent(agent_cfg, prompt, cfg, cwd, log_path, tmp_prompt):
        # 每轮都自报收敛，但每轮都把文件大改成完全不同的内容（相似度≈0）
        Path(log_path).write_text("OPTIMIZE_CONVERGED\n", encoding="utf-8")
        state["n"] += 1
        block = "ABCDEFGH"[state["n"] % 8] * 600
        (Path(cwd) / "work.md").write_text(block, encoding="utf-8")

        class P:
            returncode = 0
            def poll(self): return 0
        return P()

    monkeypatch.setattr(engine, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "wait_with_timeout", lambda proc, t: True)

    cfg = engine.load_engine_config(tmp_path)
    cfg["order"] = ["claude", "codex"]
    status = engine.run_iteration(cfg, target, "改清晰", tmp_path / "rt", max_rounds=4)

    # marker 出现但每轮都大改 -> 不提前收敛，跑满 4 轮
    assert status["rounds_run"] == 4
    assert status["stop_reason"] == "reached_max_rounds"
    assert all(r["outcome"] == "edited" for r in status["rounds"])


# 6e. 文件真正稳定（几乎没变）时仍按收敛提前停
def test_converges_when_file_stabilizes(monkeypatch, tmp_path):
    target = tmp_path / "t.md"
    target.write_text("稳定的内容" * 50, encoding="utf-8")

    def fake_run_agent(agent_cfg, prompt, cfg, cwd, log_path, tmp_prompt):
        Path(log_path).write_text("done\n", encoding="utf-8")
        # 不动文件 -> 相似度=1 -> not changed -> 收敛

        class P:
            returncode = 0
            def poll(self): return 0
        return P()

    monkeypatch.setattr(engine, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "wait_with_timeout", lambda proc, t: True)

    cfg = engine.load_engine_config(tmp_path)
    cfg["order"] = ["claude", "codex"]
    status = engine.run_iteration(cfg, target, "改清晰", tmp_path / "rt", max_rounds=6)
    assert status["stop_reason"] == "converged"
    assert status["rounds_run"] == 2  # 跑满一个 ping-pong 后即收敛


# 6f. 失败/超时轮回滚：损坏 work 不会污染下一轮，也不会成为最终成品
def test_failed_round_rolls_back(monkeypatch, tmp_path):
    target = tmp_path / "t.md"
    target.write_text("原始好内容" * 20, encoding="utf-8")

    state = {"n": 0}

    def fake_run_agent(agent_cfg, prompt, cfg, cwd, log_path, tmp_prompt):
        Path(log_path).write_text("done\n", encoding="utf-8")
        work = Path(cwd) / "work.md"
        state["n"] += 1
        if state["n"] == 1:
            work.write_text("第一轮的良好改写版本。" * 20, encoding="utf-8")  # edited（成功）

        class P:
            # 第 2 轮：把文件写成半成品后超时被杀
            returncode = 0 if state["n"] == 1 else 1
            def poll(self): return self.returncode
        if state["n"] == 2:
            work.write_text("半成", encoding="utf-8")  # 损坏/截断
        return P()

    # 第 2 轮模拟超时（wait_with_timeout 返回 False -> outcome failed）
    monkeypatch.setattr(engine, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "wait_with_timeout",
                        lambda proc, t: state["n"] != 2)

    cfg = engine.load_engine_config(tmp_path)
    cfg["order"] = ["claude", "codex"]
    status = engine.run_iteration(cfg, target, "改清晰", tmp_path / "rt", max_rounds=4)

    final_text = Path(status["final_file"]).read_text(encoding="utf-8")
    assert final_text != "半成"  # 半成品绝不能成为成品
    assert "第一轮的良好改写版本" in final_text  # 回滚到上一份成功产物
    assert any(r["outcome"] == "failed" and r.get("rolled_back") for r in status["rounds"])


# 6g. 单个 agent 全程失败 -> status.warnings 给出告警（不靠"连续失败"才发现）
def test_broken_agent_surfaced_in_warnings(monkeypatch, tmp_path):
    target = tmp_path / "t.md"
    target.write_text("内容" * 30, encoding="utf-8")

    def fake_run_agent(agent_cfg, prompt, cfg, cwd, log_path, tmp_prompt):
        work = Path(cwd) / "work.md"
        if agent_cfg["command"].startswith("claude"):
            Path(log_path).write_text("Error: 401 unauthorized\n", encoding="utf-8")

            class Bad:
                returncode = 1
                def poll(self): return 1
            return Bad()
        # codex 正常小改
        Path(log_path).write_text("done\n", encoding="utf-8")
        work.write_text(work.read_text(encoding="utf-8") + "x", encoding="utf-8")

        class Ok:
            returncode = 0
            def poll(self): return 0
        return Ok()

    monkeypatch.setattr(engine, "run_agent", fake_run_agent)
    monkeypatch.setattr(engine, "wait_with_timeout", lambda proc, t: True)

    cfg = engine.load_engine_config(tmp_path)
    cfg["order"] = ["claude", "codex"]
    status = engine.run_iteration(cfg, target, "改清晰", tmp_path / "rt", max_rounds=4)

    assert status["agent_stats"]["claude"]["failed"] == status["agent_stats"]["claude"]["rounds"]
    assert any("claude" in w for w in status["warnings"])


# 6h. order 写了未知 agent -> 该轮判失败并带 hint，不崩溃
def test_unknown_agent_does_not_crash(monkeypatch, tmp_path):
    target = tmp_path / "t.md"
    target.write_text("内容", encoding="utf-8")
    monkeypatch.setattr(engine, "run_agent",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该被调用")))
    monkeypatch.setattr(engine, "wait_with_timeout", lambda proc, t: True)

    cfg = engine.load_engine_config(tmp_path)
    cfg["order"] = ["ghost"]  # agents 里没有
    status = engine.run_iteration(cfg, target, "改清晰", tmp_path / "rt", max_rounds=2)
    assert status["result"] == "failed"
    assert all("配置缺少 agent" in (r.get("error_hint") or "") for r in status["rounds"])


# 7. check_inputs 无 target 且非 dry-run -> 失败
def test_check_inputs_requires_target(monkeypatch, tmp_path):
    monkeypatch.setattr(steps, "RUN_ROOT", tmp_path / "rt")
    wf = discover_workflow("ai_file_iterate")
    runner = WorkflowRunner(tmp_path / "runs")
    run = runner.run(wf, inputs={"dry_run": False, "args": []}, dry_run=False)
    assert run.status not in ("success", "dry_run_success")
