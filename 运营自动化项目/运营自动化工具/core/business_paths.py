"""业务路径表 —— 把「具体业务的文件 / 目录命名」从通用路径引擎里分离出来。

通用引擎 `core/config_loader.py` 只负责机制：锚点推导、paths.yaml / 环境变量覆盖、
惰性微信探测、缓存与报错。**具体有哪些业务路径 key、叫什么名字、落在哪个子目录，
全部声明在本文件**。

➡️ 别人 clone 框架后，只需把本文件换成自己业务的路径表，无需改动引擎代码。

约定：
- `business_paths(...)` 收到已解析好的 4 个锚点（project / store / brain / home），
  返回 `{key: Path}`，与框架通用路径合并。
- `apply_post_merge(...)` 在「默认值 + 配置文件 + 环境变量」全部合并完成后调用，
  用来处理「某个 key 需要跟随另一个被覆盖的 key 重新推导」这类业务联动。
"""

from __future__ import annotations

from pathlib import Path

# 默认 NAS 挂载点（org 相关，可被 paths.local.yaml / 环境变量覆盖）。
DEFAULT_NAS_MOUNT = Path("/Volumes/suolong.synology.me")

# NAS 产品根目录相对挂载点的子路径（业务约定）。
_NAS_PRODUCT_SUBPATH = ("产品资料（运营）", "1.产品资料")


def business_paths(*, project: Path, store: Path, brain: Path, home: Path) -> dict[str, Path]:
    """从 4 个锚点推导出全部业务路径，无需任何配置文件即可工作。"""
    master = store / "主数据"
    brush = store / "刷单数据"
    downloads = home / "Downloads"
    desktop = home / "Desktop"
    runtime = project / "runtime"
    nas_index = runtime / "nas_index"
    nas_mount = DEFAULT_NAS_MOUNT

    return {
        # ① 仓库自身（随仓库走）
        "pickup_watch_config": project / "config" / "pickup_watch.json",
        # AI 知识库同步默认路径（锚点推导，免手填）：
        #   source-root = 项目根（含「运营自动化工具」的那层）；kb-root = 同级 ai知识库。
        "ai_kb_source_root": store,
        "ai_kb_root": store.parent / "ai知识库",
        "nas_index_dir": nas_index,
        "nas_index_json": nas_index / "company_nas_tree.json",
        "nas_index_md": nas_index / "company_nas_tree.md",
        "nas_index_csv": nas_index / "company_nas_files.csv",
        "brush_register_pattern": Path("天猫超市*月刷单登记明细.xlsx"),
        # ② 电商Brain 目录树内（主数据 / 刷单数据 / 产品库 随仓库结构走）
        "ecommerce_brain_dir": brain,
        "product_library_dir": brain / "01-产品库",
        "nas_product_library_dir": brain / "01-产品库",
        "reimbursement_dir": brush,
        "brush_register_dir": brush,
        "backup_dir": brush / "备份",
        "brush_orders_dir": brush / "今日刷单表格",
        "brush_product_file": brush / "今日刷单产品表.xlsx",
        "jst_product_file": master / "聚水潭商品资料（最新）.xlsx",
        "jst_product_master_file": master / "聚水潭商品资料（最新）.xlsx",
        "maochao_goods_master_file": master / "猫超商品列表导出 (最新）.xlsx",
        "tmall_goods_master_file": master / "猫超商品列表导出 (最新）.xlsx",
        "massage_chair_mapping_file": master / "按摩椅资料表.xlsx",
        "massage_title_library_file": master / "按摩器材爆款标题库.xlsx",
        # ③ 系统标准目录（从 Path.home() 推导）
        "tmall_bill_download_dir": downloads,
        "tmall_hdb_glob": downloads / "HDB*.xlsx",
        "tmall_statement_list_file": downloads / "对账单列表.xlsx",
        "tmall_goods_import_file": downloads / "猫超商品列表导出.xlsx",
        "jst_product_import_file": downloads / "聚水潭商品资料（最新）.xlsx",
        "maochao_monthly_bill_dir": desktop,
        "maochao_work_dir": desktop,
        "buyer_show_output_dir": desktop,
        # ④ 机器 / org 相关：NAS 给默认挂载点。
        "company_nas_mount": nas_mount,
        "company_nas_product_root": nas_mount.joinpath(*_NAS_PRODUCT_SUBPATH),
    }


def apply_post_merge(merged: dict[str, Path], explicit: set[str]) -> None:
    """合并完成后的业务联动：NAS 产品根目录跟随最终挂载点（除非被显式覆盖）。"""
    if "company_nas_product_root" not in explicit and "company_nas_mount" in merged:
        merged["company_nas_product_root"] = merged["company_nas_mount"].joinpath(
            *_NAS_PRODUCT_SUBPATH
        )
