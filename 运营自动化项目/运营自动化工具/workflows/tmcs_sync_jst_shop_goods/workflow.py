"""猫超商品信息同步聚水潭 workflow 定义。

把 skills/tmcs_sync_jst_shop_goods 的 run 流程拆成 6 个有状态步骤；真实逻辑全部复用
skill 实现。旧命令 `python3 run.py 聚水潭商品信息同步猫超 ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_sync_jst_shop_goods import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_sync_jst_shop_goods",
        "猫超商品信息同步聚水潭",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("load_tmcs_goods", "解析商品ID", steps.load_tmcs_goods),
            step("query_tmcs_stock", "查询猫超库存", steps.query_tmcs_stock),
            step("build_jst_import_excel", "生成聚水潭导入表", steps.build_jst_import_excel),
            step("import_jst_shop_goods", "导入聚水潭店铺商品", steps.import_jst_shop_goods),
            step("collect_artifacts", "收集产物", steps.collect_artifacts),
        ],
    )
