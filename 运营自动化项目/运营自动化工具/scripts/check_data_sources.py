#!/usr/bin/env python3
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config_loader import get_path  # noqa: E402


@dataclass(frozen=True)
class Source:
    task: str
    name: str
    key: str
    kind: str
    temporary: bool = False
    optional: bool = False


SOURCES = [
    Source("append_brush_orders", "今日刷单源表目录", "brush_orders_dir", "dir", temporary=True),
    Source("append_brush_orders", "刷单登记表目录", "brush_register_dir", "dir"),
    Source("append_brush_orders", "聚水潭商品资料主源", "jst_product_master_file", "file"),
    Source("append_brush_orders", "今日刷单产品表", "brush_product_file", "file"),
    Source("append_brush_orders", "微信文件目录", "wechat_file_dir", "dir", optional=True),
    Source("tag_jst_brush_orders", "最新刷单订单 JSON", "runtime_dir", "runtime_latest_brush_orders", temporary=True),
    Source("jst_brush_reimburse_workorder", "刷单登记表目录", "brush_register_dir", "dir"),
    Source("buyer_show", "刷单登记表目录", "brush_register_dir", "dir"),
    Source("buyer_show", "买家秀 zip 输出目录", "buyer_show_output_dir", "dir"),
    Source("process_maochao_bills", "HDB 临时源文件", "tmall_hdb_glob", "glob", temporary=True, optional=True),
    Source("process_maochao_bills", "HDB 下载目录", "tmall_bill_download_dir", "dir", temporary=True),
    Source("process_maochao_bills", "对账单列表", "tmall_statement_list_file", "file", temporary=True, optional=True),
    Source("process_maochao_bills", "猫超商品列表主源", "tmall_goods_master_file", "file"),
    Source("process_maochao_bills", "聚水潭商品资料主源", "jst_product_master_file", "file"),
    Source("update_maochao_goods", "猫超商品临时导出表", "tmall_goods_import_file", "file", temporary=True, optional=True),
    Source("update_maochao_goods", "猫超商品列表主源", "tmall_goods_master_file", "file"),
    Source("update_maochao_goods", "聚水潭商品资料主源", "jst_product_master_file", "file"),
    Source("update_jst_products", "聚水潭商品临时导出表", "jst_product_import_file", "file", temporary=True, optional=True),
    Source("update_jst_products", "电商Brain 同步根目录", "ecommerce_brain_dir", "dir"),
    Source("update_jst_products", "聚水潭商品资料主源", "jst_product_master_file", "file"),
    Source("company_nas_listing", "NAS 产品资料根目录", "company_nas_product_root", "dir", optional=True),
    Source("company_nas_listing", "NAS 索引 JSON", "nas_index_json", "file", optional=True),
    Source("company_nas_listing", "产品库目录", "nas_product_library_dir", "dir"),
    Source("company_nas_listing", "聚水潭商品资料主源", "jst_product_master_file", "file"),
    Source("company_nas_index", "NAS 产品资料根目录", "company_nas_product_root", "dir", optional=True),
    Source("company_nas_index", "NAS 索引目录", "nas_index_dir", "dir", optional=True),
]


def resolve_path(source: Source) -> Path:
    path = get_path(source.key)
    if source.kind == "runtime_latest_brush_orders":
        return path / "latest_brush_orders.json"
    return path


def exists_for_kind(path: Path, kind: str) -> bool:
    if kind == "dir":
        return path.is_dir()
    if kind == "glob":
        return any(path.parent.glob(path.name))
    return path.exists()


def main() -> int:
    rows = []
    for source in SOURCES:
        path = resolve_path(source)
        exists = exists_for_kind(path, source.kind)
        rows.append(
            {
                "task": source.task,
                "name": source.name,
                "key": source.key,
                "path": str(path),
                "status": "OK" if exists else ("MISSING_OPTIONAL" if source.optional else "MISSING"),
                "temporary": "yes" if source.temporary else "no",
            }
        )

    widths = {
        "task": max(len("task"), *(len(row["task"]) for row in rows)),
        "status": max(len("status"), *(len(row["status"]) for row in rows)),
        "temporary": len("temporary"),
    }
    print(f"{'task':<{widths['task']}}  {'status':<{widths['status']}}  temporary  key -> path")
    print("-" * 120)
    for row in rows:
        print(
            f"{row['task']:<{widths['task']}}  "
            f"{row['status']:<{widths['status']}}  "
            f"{row['temporary']:<9}  "
            f"{row['key']} -> {row['path']}"
        )

    required_missing = [row for row in rows if row["status"] == "MISSING"]
    if required_missing:
        print(f"\n必需数据源缺失：{len(required_missing)}")
        return 1
    print("\n必需数据源检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
