from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_sku_roi import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_sku_roi",
        "猫超单品ROI测算",
        [
            step("check_inputs", "校验输入与文件", steps.check_inputs),
            step("lookup_tmcs_barcode", "查询猫超条码", steps.lookup_tmcs_barcode),
            step("lookup_jst_product", "查询聚水潭商品", steps.lookup_jst_product),
            step("calculate_roi", "计算ROI", steps.calculate_roi),
            step("collect_outputs", "汇总结果与产物", steps.collect_outputs),
        ],
    )

