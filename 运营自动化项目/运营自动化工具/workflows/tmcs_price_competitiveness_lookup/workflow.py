"""猫超价格竞争力商品查询 workflow 定义。

属"平台读取 + workflow 业务判断"类型：
- 页面交互（进入价格竞争力页、翻页读整张列表）由 Ops-Cli
  `tmcs price-competitiveness list` 完成，本层不碰 URL/Cookie/Token/Selector/Playwright/CDP。
- 本层负责：整张列表按天缓存、单个 / 批量商品编码精确匹配、输出「存在 / 不存在」。
  当天首查抓一次并缓存，后续全部走缓存秒出；跨天 / --refresh 时重抓。
"""

from __future__ import annotations

from core.runtime import Workflow, build_workflow as _make_workflow, step

from workflows.tmcs_price_competitiveness_lookup import steps


def build_workflow() -> Workflow:
    return _make_workflow(
        "tmcs_price_competitiveness_lookup",
        "猫超价格竞争力查询",
        [
            step("check_inputs", "校验商品编码（单个 / 批量）", steps.check_inputs),
            step("load_list", "加载整张列表（当天缓存优先）", steps.load_list),
            step("match_codes", "逐个精确匹配商品编码", steps.match_codes),
            step("collect_outputs", "汇总存在 / 不存在", steps.collect_outputs),
        ],
    )
