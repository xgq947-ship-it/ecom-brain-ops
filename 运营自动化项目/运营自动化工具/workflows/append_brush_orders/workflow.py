"""刷单表格登记 workflow 定义。

拆成 5 个有状态步骤；微信识别/解析/去重/登记表补丁/打标等业务逻辑在同包 appender.py，
append 主体整体编排 appender.run()。旧命令 `python3 run.py 刷单表格登记 ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.append_brush_orders import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "append_brush_orders",
        "刷单表格登记",
        [
            step("check_inputs", "解析参数并配置路径", steps.check_inputs),
            step("load_source_orders", "检查源表目录", steps.load_source_orders),
            step("validate_orders", "校验主数据文件", steps.validate_orders),
            step("append_to_register", "追加登记(wrapper)", steps.append_to_register),
            step("collect_artifacts", "收集产物", steps.collect_artifacts),
        ],
    )
