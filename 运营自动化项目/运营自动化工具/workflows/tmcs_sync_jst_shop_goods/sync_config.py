"""猫超商品信息同步聚水潭 workflow 的配置常量与输出目录。

由 skills/tmcs_sync_jst_shop_goods/config.py 折叠而来；输出从原 skill 本地目录改到
runtime/artifacts（经 core.config_loader 锚点推导），不再把运行产物写进源码目录。
"""

from __future__ import annotations

from core.config_loader import get_path

DEFAULT_WAREHOUSE_CODE = "mc_aokesi_suolong"
DEFAULT_JST_SHOP_NAME = "（猫超）福安市启明工贸有限公司（肖国清）"

OUTPUT_DIR = get_path("runtime_dir") / "artifacts" / "tmcs_sync_jst_shop_goods"


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
