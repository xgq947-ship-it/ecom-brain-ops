"""更新猫超商品列表 workflow 定义。

把 tasks/tmall_product_list 拆成 6 个有状态步骤；平台同步复用 legacy 的 ops 调用。
旧命令 `python3 run.py 更新猫超商品列表 ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmall_product_list import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmall_product_list",
        "更新猫超商品列表",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("check_local_source", "检查本地导入表", steps.check_local_source),
            step("download_tmcs_products", "同步猫超商品", steps.download_tmcs_products),
            step("validate_products", "校验同步结果", steps.validate_products),
            step("update_master_data", "写入主表", steps.update_master_data),
            step("collect_artifacts", "收集产物", steps.collect_artifacts),
        ],
    )
