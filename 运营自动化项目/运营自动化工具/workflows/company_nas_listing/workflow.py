"""公司网盘下载产品 workflow 定义。

拆成 6 个有状态步骤；选材/复制/匹配/Excel 等业务逻辑在同包 listing.py。
旧命令 `python3 run.py 公司网盘下载产品 ...` / `python3 run.py company_nas_listing ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.company_nas_listing import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "company_nas_listing",
        "公司网盘下载产品",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("parse_listing_request", "解析型号请求", steps.parse_listing_request),
            step("search_nas_index", "定位网盘素材", steps.search_nas_index),
            step("copy_product_assets", "复制产品素材", steps.copy_product_assets),
            step("build_listing_data", "生成上架数据", steps.build_listing_data),
            step("collect_artifacts", "校验并卸载", steps.collect_artifacts),
        ],
    )
