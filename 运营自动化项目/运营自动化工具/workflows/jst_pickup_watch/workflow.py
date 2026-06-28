"""聚水潭揽收监控 workflow 定义。

把 tasks/jst_pickup_watch.py 拆成 6 个有状态步骤；真实业务逻辑全部复用 legacy 实现。
旧命令 `python3 run.py 聚水潭揽收监控` 不受影响，仍走 tasks/jst_pickup_watch.py。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_pickup_watch import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_pickup_watch",
        "聚水潭揽收监控",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("load_config", "加载监控配置", steps.load_config),
            step("fetch_pickup_watch_data", "拉取揽收数据", steps.fetch_pickup_watch_data),
            step("analyze_abnormal_orders", "分析异常订单", steps.analyze_abnormal_orders),
            step("notify_if_needed", "按需发送提醒", steps.notify_if_needed),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
