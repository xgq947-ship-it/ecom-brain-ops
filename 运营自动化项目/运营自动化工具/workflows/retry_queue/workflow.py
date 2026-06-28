"""失败任务重试队列 workflow 定义。

把 tasks/retry_queue.py 拆成 5 个有状态步骤；队列与重放复用 core/retry_queue.py。
旧命令 `python3 run.py 查看失败任务` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.retry_queue import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "retry_queue",
        "失败任务重试队列",
        [
            step("check_inputs", "解析参数与模式", steps.check_inputs),
            step("load_retry_items", "加载待重试项", steps.load_retry_items),
            step("preview_retry", "预览重放计划", steps.preview_retry),
            step("execute_retry", "执行重放/标记", steps.execute_retry),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
