"""买家秀文件整理 workflow 定义。

移植 Hermes skill `organize-buyer-show`：对买家秀数据包执行两步整理——
删除图片 ≤N 张的低质量买家秀、去掉 SKU 层级平铺到根目录。

纯本地文件操作，不涉及平台调用。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.organize_buyer_show import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "organize_buyer_show",
        "买家秀文件整理（删低质 + 去 SKU 层级平铺）",
        [
            step("check_inputs", "解析参数、校验路径", steps.check_inputs),
            step("scan_preview", "递归扫描并分类预览", steps.scan_preview),
            step("delete_low_quality", "删除低质买家秀(≤阈值)", steps.delete_low_quality),
            step("flatten_sku", "去 SKU 层级平铺到根目录", steps.flatten_sku),
            step("verify_collect", "验证并汇总最终状态", steps.verify_collect),
        ],
    )
