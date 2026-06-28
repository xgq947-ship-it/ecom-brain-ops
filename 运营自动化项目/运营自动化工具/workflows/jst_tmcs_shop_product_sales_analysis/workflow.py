"""猫超店铺商品销售分析 workflow 定义。

下载指定月份的聚水潭「商品销售情况.csv」（下载动作在 Ops-Cli），再调用集成的 CSV 分析脚本，
输出店铺款式编码列表。中文入口 `猫超店铺商品销售分析`。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.jst_tmcs_shop_product_sales_analysis import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "jst_tmcs_shop_product_sales_analysis",
        "猫超店铺商品销售分析",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("fetch_sales_csv", "获取商品销售CSV", steps.fetch_sales_csv),
            step("analyze_sales_csv", "分析销售并输出店铺款式编码", steps.analyze_sales_csv),
            step("write_outputs", "写出结果", steps.write_outputs),
            step("collect_artifacts", "收集产物", steps.collect_artifacts),
        ],
    )
