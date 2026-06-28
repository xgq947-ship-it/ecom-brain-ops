"""天猫商品价格监控 workflow 定义。

属「平台读取 + workflow 业务判断」类型：
- 天猫公开商品页实时价格读取（mtop 结构化到手价 → DOM/文本兜底 → 截图）全部由
  Ops-Cli `tmall price get` 完成，本层不碰 URL/Cookie/Token/Selector/Playwright/CDP。
- 本层只负责：解析输入、查控价、控价对比、差价计算、状态判定、登录失效记录、产出 Excel/JSON。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmall_price_monitor import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmall_price_monitor",
        "天猫商品价格监控",
        [
            step("check_inputs", "解析商品ID输入", steps.check_inputs),
            step("resolve_control_prices", "匹配淘系控价（猫超条码→聚水潭）", steps.resolve_control_prices),
            step("fetch_realtime_prices", "读取天猫实时价格", steps.fetch_realtime_prices),
            step("compare_prices", "控价对比与状态判定", steps.compare_prices),
            step("notify_login_if_needed", "登录态失效记录", steps.notify_login_if_needed),
            step("write_outputs", "产出 Excel 与 JSON", steps.write_outputs),
            step("collect_outputs", "汇总结果", steps.collect_outputs),
        ],
    )
