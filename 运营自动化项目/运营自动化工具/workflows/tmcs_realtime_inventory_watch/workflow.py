"""猫超库存实时监测 workflow 定义。

复用既有平台下载能力（聚水潭商品资料同步 / 猫超库存导出），读取并合并 3 张表，
输出存在库存风险的 SKU。属"平台读取 + workflow 业务判断"类型，本层不写平台逻辑。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_realtime_inventory_watch import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_realtime_inventory_watch",
        "猫超库存实时监测",
        [
            step("check_inputs", "校验参数", steps.check_inputs),
            step("refresh_jst_product_data", "刷新/读取聚水潭资料", steps.refresh_jst_product_data),
            step("refresh_tmcs_stock_data", "刷新/读取猫超库存明细", steps.refresh_tmcs_stock_data),
            step("load_maochao_goods", "读取猫超商品列表", steps.load_maochao_goods),
            step("load_jst_products", "读取聚水潭商品资料", steps.load_jst_products),
            step("load_tmcs_stock", "读取猫超库存明细", steps.load_tmcs_stock),
            step("build_inventory_table", "构建剩余库存中间表", steps.build_inventory_table),
            step("detect_inventory_risks", "判定库存风险", steps.detect_inventory_risks),
            step("write_outputs", "写出结果与产物", steps.write_outputs),
            step("notify_if_needed", "按需通知（预览）", steps.notify_if_needed),
            step("collect_outputs", "收集结果", steps.collect_outputs),
        ],
    )
