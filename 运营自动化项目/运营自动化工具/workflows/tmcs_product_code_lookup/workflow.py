from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_product_code_lookup import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_product_code_lookup",
        "猫超商品编码查询",
        [
            step("check_inputs", "校验输入与文件", steps.check_inputs),
            step("load_tmcs_products", "读取猫超商品列表并筛选上架", steps.load_tmcs_products),
            step("fuzzy_match_products", "型号模糊匹配", steps.fuzzy_match_step),
            step("collect_outputs", "汇总结果与产物", steps.collect_outputs),
        ],
    )
