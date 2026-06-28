# -*- coding: utf-8 -*-
"""ai_file_iterate 引擎：两个 AI 轮流打磨同一份文件（乒乓迭代）。

纯本地编排，只用标准库 + 调用本机 claude / codex CLI。
不碰平台、不调 Ops-Cli —— 属工具类 workflow 的核心实现模块。

对外主要入口：
  - parse_request(text)                  从一句话抽出『文件路径 + 优化标准』
  - check_agents(cfg, run_root)          体检：实际调用每个 AI，验证能否调用
  - run_iteration(cfg, target, brief...) 真正跑迭代，返回结构化状态 dict

所有运行态（running/archive/logs）写在 run_root 下；run_root 由 workflow 从
PROJECT_ROOT/runtime/ai_iterate 推导（已被 .gitignore 的 runtime/ 覆盖）。
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
OPTIMIZER_WRAPPER = HERE / "prompts" / "optimizer_wrapper.md"

ENGINE_DEFAULTS = {
    "output_file": "",
    "in_place": False,
    "order": ["claude", "codex"],
    "max_rounds": 6,
    "convergence_ratio": 0.985,
    # 自报「已收敛」标记只在本轮改动已很小（相似度 >= 此值）时才采信；
    # 否则视为还在实质打磨，无视标记继续下一轮，避免第 2 轮就被一句"已收敛"叫停。
    "marker_min_ratio": 0.95,
    "per_round_timeout_minutes": 40,
    "min_consecutive_fail_abort": 2,
    "prompt_mode": "stdin",
    "strip_parent_claude_env": True,
    "agents": {
        "claude": {"command": "claude",
                   "extra_args": ["-p", "--dangerously-skip-permissions"],
                   "env_file": ""},
        "codex": {"command": "codex exec",
                  "extra_args": ["--sandbox", "workspace-write", "--skip-git-repo-check"]},
    },
    "limit_keywords": ["usage limit", "rate limit", "limit reached",
                       "try again later", "exceeded", "too many requests", "quota"],
    "converged_markers": ["OPTIMIZE_CONVERGED", "已收敛"],
}

AUTH_ERROR_PATTERNS = [
    "401", "invalid authentication", "authenticate", "unauthorized",
    "invalid api key", "invalid x-api-key", "not logged in", "please run /login",
    "oauth", "credentials",
]

NESTED_ENV_STRIP = [
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SDK_HAS_OAUTH_REFRESH",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH", "CLAUDE_CODE_OAUTH_SCOPES",
    "CLAUDE_AGENT_SDK_VERSION", "CLAUDE_CODE_EXECPATH", "AI_AGENT", "CLAUDE_EFFORT",
]


# --------------------------------------------------------------------------- 小工具
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_text(p: Path) -> str:
    try:
        return Path(p).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def load_engine_config(project_root: Path, overrides: dict | None = None) -> dict:
    """合并默认配置 + 可选 config/ai_iterate.json 覆盖；设定密钥文件默认位置。"""
    cfg = json.loads(json.dumps(ENGINE_DEFAULTS))  # deep copy
    cfg_file = project_root / "config" / "ai_iterate.json"
    if cfg_file.exists():
        try:
            for k, v in json.loads(cfg_file.read_text(encoding="utf-8")).items():
                if k.startswith("_"):
                    continue
                if k == "agents" and isinstance(v, dict):
                    for an, ac in v.items():
                        cfg["agents"].setdefault(an, {}).update(ac)
                else:
                    cfg[k] = v
        except Exception:
            pass
    # claude 无头长期 token 的密钥文件（gitignore），默认放 config/
    if not cfg["agents"]["claude"].get("env_file"):
        cfg["agents"]["claude"]["env_file"] = str(project_root / "config" / "ai_iterate.secret.env")
    if overrides:
        cfg.update({k: v for k, v in overrides.items() if v is not None})
    return cfg


# --------------------------------------------------------------------------- 自然语言请求
_FILLER = ["给我", "帮我", "请", "麻烦", "优化", "一下", "这个", "那个", "文件",
           "把", "改", "改进", "润色", "优化一下", "下", "的", "，", ",", "。",
           "：", ":", " ", "\t", "\n", '"', "'", "`"]


def parse_request(text: str) -> tuple[str | None, str]:
    if not text:
        return None, ""
    text = text.strip()
    whole = os.path.expanduser(text)
    if os.path.isfile(whole):
        return whole, ""
    candidates: list[str] = []
    for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'', text):
        candidates.append(m.group(1) or m.group(2))
    for m in re.finditer(r'(?:~|\./|/)[^\s"\']+', text):
        candidates.append(m.group(0))
    idx = text.find("/")
    if idx != -1:
        candidates.append(text[idx:].strip())
    tilde = text.find("~/")
    if tilde != -1:
        candidates.append(text[tilde:].strip())
    target = matched_raw = None
    for c in sorted(set(candidates), key=len, reverse=True):
        p = os.path.expanduser(c.strip().strip("\"'"))
        if os.path.isfile(p):
            target, matched_raw = p, c
            break
    if not target:
        return None, ""
    brief = text.replace(matched_raw, "") if matched_raw else text
    for c in candidates:
        if os.path.expanduser(c.strip().strip("\"'")) == target:
            brief = brief.replace(c, "")
    brief = re.sub(r"\s+", " ", brief).strip(" :：,，。.、\t\n\"'`")
    stripped = brief
    for f in _FILLER:
        stripped = stripped.replace(f, "")
    if not stripped.strip():
        brief = ""
    return target, brief


def detect_error_hint(log_text: str) -> str:
    low = log_text.lower()
    if any(p in low for p in AUTH_ERROR_PATTERNS):
        return "鉴权失败：该 AI 在当前环境登录态不可用（如 401/未登录）。常见于后台读不到订阅登录态，需 claude setup-token。"
    if any(k in low for k in ("usage limit", "rate limit", "exceeded", "too many requests", "quota")):
        return "额度/限流：该 AI 触发了用量上限或限流。"
    if "command not found" in low or "no such file" in low:
        return "命令不存在：检查 ai_iterate 配置里该 agent 的 command。"
    return ""


# --------------------------------------------------------------------------- 启动 agent
def build_agent_env(cfg: dict, agent_cfg: dict) -> dict:
    env = os.environ.copy()
    if cfg.get("strip_parent_claude_env", True):
        for k in NESTED_ENV_STRIP:
            env.pop(k, None)
    ef = agent_cfg.get("env_file")
    if ef:
        ef_p = Path(os.path.expanduser(ef))
        if ef_p.exists():
            for line in ef_p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = os.path.expandvars(v.strip().strip("\"'"))
    for k, v in agent_cfg.get("env", {}).items():
        env[k] = os.path.expandvars(str(v))
    return env


def _build_argv(command: str, extra_args, prompt: str, mode: str, tmp_file: Path):
    argv = shlex.split(command) + list(extra_args)
    stdin_text = None
    if mode == "arg":
        argv.append(prompt)
    elif mode == "file":
        tmp_file.write_text(prompt, encoding="utf-8")
        argv.append(str(tmp_file))
    else:
        stdin_text = prompt
    return argv, stdin_text


def run_agent(agent_cfg, prompt, cfg, cwd: Path, log_path: Path, tmp_prompt: Path):
    mode = cfg.get("prompt_mode", "stdin")
    argv, stdin_text = _build_argv(agent_cfg["command"], agent_cfg.get("extra_args", []),
                                   prompt, mode, tmp_prompt)
    env = build_agent_env(cfg, agent_cfg)
    logf = open(log_path, "a", encoding="utf-8", buffering=1)
    logf.write(f"\n===== 启动 {_now()} | cwd={cwd} | mode={mode} =====\n")
    logf.flush()
    proc = subprocess.Popen(
        argv, cwd=str(cwd),
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=logf, stderr=subprocess.STDOUT, text=True, start_new_session=True, env=env,
    )
    if stdin_text is not None:
        try:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        except Exception:
            pass
    return proc


def terminate_process(proc) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=15)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()


def wait_with_timeout(proc, timeout_s: float) -> bool:
    started = time.time()
    while proc.poll() is None:
        if time.time() - started > timeout_s:
            terminate_process(proc)
            return False
        time.sleep(3)
    return True


def log_hit(log_path: Path, words) -> bool:
    text = read_text(log_path).lower()
    return any(w.lower() in text for w in words)


# --------------------------------------------------------------------------- 提示词
def build_optimizer_prompt(cfg, work_path: Path, brief_text: str,
                           agent_name: str, round_idx: int, total: int) -> str:
    wrapper = read_text(OPTIMIZER_WRAPPER).strip() if OPTIMIZER_WRAPPER.exists() else ""
    other = [a for a in cfg["order"] if a != agent_name]
    other_name = other[0] if other else "另一个 AI"
    brief_block = brief_text.strip() or "（未单独提供标准：请按通用的清晰度、结构、准确性、表达力来提升。）"
    return "\n".join([
        wrapper, "", "=" * 70,
        f"# 你是本轮执行者：{agent_name}（与 {other_name} 交替打磨同一份文件）",
        f"# 当前进度：第 {round_idx} 轮 / 共上限 {total} 轮",
        "# 要优化的文件（就地读写，不要新建文件）：", f"#   {work_path}",
        "=" * 70, "",
        "# ===== 优化目标 / 标准（用户原文，未改写）=====", "", brief_block, "",
        "=" * 70,
        "# 现在：读取上面那个文件 → 在原文件就地改进 → 保存。完成后按 wrapper 说明输出结束标记。",
    ])


# --------------------------------------------------------------------------- 单轮 / 迭代
def run_round(cfg, run_id, run_dir: Path, logs_dir: Path, work_path: Path,
              brief_text: str, agent_name: str, round_idx: int, max_rounds: int) -> dict:
    agent_cfg = cfg["agents"].get(agent_name)
    if not agent_cfg:
        # order 里写了 agents 中不存在的名字：当成失败轮（带清晰 hint），不让它 TypeError 崩掉整个迭代。
        return {"round": round_idx, "agent": agent_name, "outcome": "failed",
                "similarity": 1.0, "changed": False, "exit": None, "timed_out": False,
                "converged_marker": False, "hit_limit": False,
                "error_hint": f"配置缺少 agent「{agent_name}」（检查 ai_iterate.json 的 order / agents）",
                "log": ""}
    rounds_dir = run_dir / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    tag = f"round_{round_idx:02d}_{agent_name}"
    log_path = logs_dir / f"{run_id}_{tag}.log"

    before = read_text(work_path)
    (rounds_dir / f"{tag}_before{work_path.suffix}").write_text(before, encoding="utf-8")

    prompt = build_optimizer_prompt(cfg, work_path, brief_text, agent_name, round_idx, max_rounds)
    proc = run_agent(agent_cfg, prompt, cfg, cwd=run_dir, log_path=log_path,
                     tmp_prompt=run_dir / "_prompt_input.md")
    finished = wait_with_timeout(proc, cfg["per_round_timeout_minutes"] * 60)
    exit_code = proc.returncode

    after = read_text(work_path)
    (rounds_dir / f"{tag}_after{work_path.suffix}").write_text(after, encoding="utf-8")

    ratio = similarity(before, after)
    changed = ratio < cfg["convergence_ratio"]
    marker = log_hit(log_path, cfg["converged_markers"])
    limited = log_hit(log_path, cfg["limit_keywords"])
    # 自报标记只在本轮改动已很小时才采信：agent 一边大改一边喊"已收敛"不算数（以 diff 为准）。
    marker_trusted = marker and ratio >= cfg.get("marker_min_ratio", 0.95)
    if (not finished) or (exit_code not in (0, None) and not changed) or (limited and not changed):
        outcome = "failed"
    elif (not changed) or marker_trusted:
        outcome = "converged"
    else:
        outcome = "edited"
    error_hint = detect_error_hint(read_text(log_path)) if outcome == "failed" else ""
    return {"round": round_idx, "agent": agent_name, "outcome": outcome,
            "similarity": round(ratio, 4), "changed": changed, "exit": exit_code,
            "timed_out": not finished, "converged_marker": marker, "hit_limit": limited,
            "error_hint": error_hint, "log": str(log_path)}


def finalize_output(cfg, work_path: Path, target_path: Path) -> Path:
    out = cfg.get("output_file") or ""
    dest = Path(out) if out else target_path.with_name(f"{target_path.stem}.final{target_path.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(work_path, dest)
    if cfg.get("in_place"):
        shutil.copy2(work_path, target_path)
    return dest


def _notify(on_progress: Callable[[dict], None] | None, event: dict) -> None:
    """安全地回调进度（如 NotchFlow 上报）；回调异常绝不影响迭代主流程。"""
    if on_progress is None:
        return
    try:
        on_progress(event)
    except Exception:
        pass


def run_iteration(cfg: dict, target: Path, brief_text: str, run_root: Path,
                  *, max_rounds: int | None = None,
                  on_progress: Callable[[dict], None] | None = None) -> dict:
    """真正跑乒乓迭代。返回状态 dict（含每轮、停止原因、成品路径）。

    on_progress（可选）：每轮开始/结束时回调一个进度 event dict，供上报 NotchFlow 等用。
    event 形如 {"phase": "round_start"|"round_done", "round": i, "max_rounds": n,
    "agent": name, "outcome": ..., "similarity": ...}；回调异常会被吞掉，不影响迭代。
    """
    target = Path(target)
    running = run_root / "running"
    archive = run_root / "archive"
    logs_dir = run_root / "logs"
    for d in (running, archive, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    run_id = _run_id()
    run_dir = running / run_id
    (run_dir / "rounds").mkdir(parents=True, exist_ok=True)
    work = run_dir / f"work{target.suffix or '.txt'}"
    shutil.copy2(target, work)

    order = cfg["order"]
    max_rounds = max_rounds or cfg["max_rounds"]
    convergence_ratio = cfg["convergence_ratio"]
    rounds: list[dict] = []
    consecutive_fail = 0
    stop_reason = "reached_max_rounds"
    # 最近一份"成功轮"产物：失败/超时轮会把 work 改成半成品甚至损坏态，回滚到它，
    # 避免坏内容污染下一个 agent，也避免最终 .final 拿到半成品。初始即原文件内容。
    last_good = read_text(work)
    # 每个"成功轮"结束后的稳定内容，用于震荡检测（两个 AI 来回拉锯、永不收敛）。
    content_history: list[str] = [last_good]

    for i in range(1, max_rounds + 1):
        agent_name = order[(i - 1) % len(order)]
        _notify(on_progress, {"phase": "round_start", "round": i,
                              "max_rounds": max_rounds, "agent": agent_name})
        res = run_round(cfg, run_id, run_dir, logs_dir, work, brief_text, agent_name, i, max_rounds)
        rounds.append(res)
        _notify(on_progress, {"phase": "round_done", "round": i,
                              "max_rounds": max_rounds, "agent": agent_name,
                              "outcome": res["outcome"], "similarity": res["similarity"]})
        if res["outcome"] == "failed":
            # 回滚到上一份成功产物：这一轮的任何改动（含半成品）一律丢弃。
            work.write_text(last_good, encoding="utf-8")
            res["rolled_back"] = True
            consecutive_fail += 1
            if consecutive_fail >= cfg["min_consecutive_fail_abort"]:
                stop_reason = "consecutive_failures"
                break
            continue
        consecutive_fail = 0
        last_good = read_text(work)
        content_history.append(last_good)
        if res["outcome"] == "converged" and i >= len(order):
            stop_reason = "converged"
            break
        # 震荡检测：跑满一个完整 ping-pong 后，若当前稳定态与上上个成功态几乎一致，
        # 说明两个 AI 在来回拉锯、已无实质进展，提前停（避免空跑到 max_rounds）。
        if i > len(order) and len(content_history) >= 3 \
                and similarity(content_history[-1], content_history[-3]) >= convergence_ratio:
            stop_reason = "oscillation_or_stable"
            break

    # work 此刻恒为最近一份成功产物（失败轮已回滚），finalize 不会拿到半成品 / 损坏文件。
    final_path = finalize_output(cfg, work, target)
    agent_stats: dict[str, dict] = {}
    for r in rounds:
        s = agent_stats.setdefault(r["agent"], {"rounds": 0, "edited": 0, "converged": 0, "failed": 0})
        s["rounds"] += 1
        s[r["outcome"]] = s.get(r["outcome"], 0) + 1
    warnings: list[str] = []
    for name, s in agent_stats.items():
        if s["rounds"] > 0 and s["failed"] == s["rounds"]:
            hint = next((r.get("error_hint") for r in rounds
                         if r["agent"] == name and r.get("error_hint")), "")
            warnings.append(
                f"{name} 全程未生效（{s['rounds']} 轮均失败）" + (f"：{hint}" if hint else "")
            )
    status = {
        "run_id": run_id, "mode": "iterate", "target_file": str(target),
        "final_file": str(final_path), "order": order, "max_rounds": max_rounds,
        "rounds_run": len(rounds), "stop_reason": stop_reason, "rounds": rounds,
        "agent_stats": agent_stats, "warnings": warnings,
        "finished_at": _now(),
        "result": "completed" if stop_reason != "consecutive_failures" else "failed",
    }
    (run_dir / "final_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    # 归档
    dest = archive / run_id
    if dest.exists():
        dest = archive / f"{run_id}_{int(time.time())}"
    for lg in logs_dir.glob(f"{run_id}_*.log"):
        shutil.copy2(lg, run_dir / "rounds" / lg.name)
    shutil.move(str(run_dir), str(dest))
    status["archive_dir"] = str(dest)
    return status


def agents_resolvable(cfg: dict) -> dict:
    """只查命令是否可找到（不调用 AI），供 dry-run 用。"""
    out = {}
    for name in cfg["order"]:
        cmd = cfg["agents"].get(name, {}).get("command", "")
        ok = bool(cmd.strip()) and shutil.which(shlex.split(cmd)[0]) is not None
        out[name] = {"command": cmd, "found": ok}
    return out


def check_agents(cfg: dict, run_root: Path) -> dict:
    """逐个真实调用 agent，返回 {name: {ok, hint}}。只发一句话。"""
    import tempfile
    result = {}
    for name in cfg["order"]:
        ac = cfg["agents"].get(name, {})
        cmd = ac.get("command", "")
        if not cmd.strip() or shutil.which(shlex.split(cmd)[0]) is None:
            result[name] = {"ok": False, "hint": f"命令找不到: {cmd!r}"}
            continue
        with tempfile.TemporaryDirectory(dir=str(run_root) if run_root.exists() else None) as td:
            logp = Path(td) / "check.log"
            proc = run_agent(ac, "请只回复两个字：OK。不要做任何其他事。", cfg,
                             cwd=Path(td), log_path=logp, tmp_prompt=Path(td) / "p.md")
            finished = wait_with_timeout(proc, 90)
            out = read_text(logp)
            hint = detect_error_hint(out)
            if not finished:
                result[name] = {"ok": False, "hint": "超时（90s 未返回）"}
            elif hint:
                result[name] = {"ok": False, "hint": hint}
            elif proc.returncode not in (0, None):
                result[name] = {"ok": False, "hint": f"退出码 {proc.returncode}"}
            else:
                result[name] = {"ok": True, "hint": ""}
    return result
