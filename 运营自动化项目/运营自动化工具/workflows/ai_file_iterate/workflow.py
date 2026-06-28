"""AI 文件迭代优化 workflow 定义（两个 AI 轮流打磨同一份文件）。

工具类 workflow：不碰平台、不调 Ops-Cli，纯本地编排 claude / codex CLI。
3 个状态步骤：解析请求 -> 跑迭代（dry-run 只体检）-> 收集结果。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.ai_file_iterate import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "ai_file_iterate",
        "AI 文件迭代优化",
        [
            step("check_inputs", "解析请求/文件/标准", steps.check_inputs),
            step("iterate", "两个 AI 轮流打磨", steps.iterate),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
