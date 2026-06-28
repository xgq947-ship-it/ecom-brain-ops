"""买家秀 workflow 定义。

拆成 6 个有状态步骤；订单匹配/分组选图/轮换/打 zip/登记表补丁等业务逻辑在同包 packager.py。
旧命令 `python3 run.py buyer_show ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.buyer_show import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "buyer_show",
        "买家秀自动分组压缩登记",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("scan_buyer_show_sources", "匹配登记表订单", steps.scan_buyer_show_sources),
            step("select_groups", "规划分组轮询", steps.select_groups),
            step("build_zip_packages", "打包买家秀压缩包", steps.build_zip_packages),
            step("update_register", "回写登记表与轮询", steps.update_register),
            step("collect_artifacts", "收集产物", steps.collect_artifacts),
        ],
    )
