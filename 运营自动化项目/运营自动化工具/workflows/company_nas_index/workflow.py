"""更新公司网盘索引 workflow 定义。

把 tasks/company_nas_index.py 拆成 5 个有状态步骤；扫描/索引/搜索复用 legacy。
旧命令 `python3 run.py 更新公司网盘索引 ...` 不受影响。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.company_nas_index import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "company_nas_index",
        "更新公司网盘索引",
        [
            step("check_inputs", "解析参数", steps.check_inputs),
            step("scan_nas", "扫描网盘/搜索", steps.scan_nas),
            step("build_index", "汇总索引", steps.build_index),
            step("save_index", "写出索引文件", steps.save_index),
            step("collect_artifacts", "收集产物并卸载", steps.collect_artifacts),
        ],
    )
