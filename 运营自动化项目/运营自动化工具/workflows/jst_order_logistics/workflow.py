"""聚水潭订单物流轨迹查询 workflow 定义。

把"查询物流轨迹"这一平台读取动作 step 化，真实平台调用全部委托给
Ops-Cli 的 `ops jst order logistics`（业务层不直连聚水潭）。
输出对齐 ops-cli 契约：collect 步骤回填 {success, platform, command, data}。
旧中文命令 `python3 run.py 物流查询` 通过 tasks/jst_order_logistics.py 走本 workflow。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_order_logistics import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_order_logistics",
        "聚水潭订单物流查询",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("fetch_logistics", "查询物流轨迹", steps.fetch_logistics),
            step("write_output", "写出查询结果", steps.write_output),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
