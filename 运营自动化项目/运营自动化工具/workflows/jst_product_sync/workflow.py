"""更新聚水潭商品资料 workflow 定义。

把 tasks/jst_product_sync 拆成 6 个有状态步骤；平台同步复用 legacy 的 ops 调用。
旧命令 `python3 run.py 更新聚水潭资料 ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_product_sync import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_product_sync",
        "更新聚水潭商品资料",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("check_local_source", "检查本地源文件", steps.check_local_source),
            step("download_jst_products", "同步聚水潭资料", steps.download_jst_products),
            step("validate_products", "校验同步结果", steps.validate_products),
            step("update_master_data", "写入主数据", steps.update_master_data),
            step("collect_artifacts", "收集产物", steps.collect_artifacts),
        ],
    )
