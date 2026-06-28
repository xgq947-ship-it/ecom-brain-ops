"""AI 知识库同步更新 workflow 定义。

定位：
- 固定提示词 + 最新项目扫描 + 目标文件清单 + 校验入口
- 当前 AI 会话负责理解和生成更新内容
- workflow 负责沉淀 prompt bundle、按 updates.json 落盘、调用 validate 脚本
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.ai_knowledge_base_update import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "ai_knowledge_base_update",
        "AI知识库同步更新",
        [
            step("check_inputs", "检查输入参数", steps.check_inputs),
            step("collect_context", "扫描最新项目与目标文档", steps.collect_context),
            step("build_update_bundle", "生成固定提示词与更新请求包", steps.build_update_bundle),
            step("apply_updates", "按当前 AI 会话产出的 updates.json 落盘", steps.apply_updates),
            step("regenerate_dispatch", "从 frontmatter 确定性重建派发表", steps.regenerate_dispatch),
            step("validate_knowledge_base", "校验知识库完整性", steps.validate_knowledge_base),
            step("collect_outputs", "汇总结果", steps.collect_outputs),
        ],
    )

