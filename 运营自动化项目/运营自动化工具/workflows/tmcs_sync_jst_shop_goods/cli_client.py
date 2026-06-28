from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from clients.ops_cli_client import run_ops_json  # noqa: E402

STANDARD_FIELDS = ["platform_item_id", "platform_sku_id", "supplier_goods_id", "merchant_goods_code"]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _run_ops(args: list[str], *, interactive_recovery: bool = True) -> Any:
    return run_ops_json(args, interactive_recovery=interactive_recovery)


def _standardize(row: dict[str, Any]) -> dict[str, str]:
    return {field: _text(row.get(field)) for field in STANDARD_FIELDS}


def query_tmcs_stock(*, item_ids: list[str], warehouse_code: str) -> list[dict[str, str]]:
    payload = _run_ops(
        [
            "tmcs",
            "stock",
            "query",
            "--item-ids",
            ",".join(item_ids),
            "--warehouse-code",
            warehouse_code,
            "--output",
            "json",
        ]
    )
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("rows"), list):
            payload = data["rows"]
        elif isinstance(data, list):
            payload = data
    if not isinstance(payload, list):
        raise RuntimeError("Ops-Cli JSON 结构异常，tmcs stock query 期望返回 data.rows 数组。")
    return [_standardize(row) for row in payload if isinstance(row, dict)]


def learn_jst_shop_goods_import() -> dict[str, Any]:
    return _run_ops(
        ["--json", "jst", "browser", "learn", "--scene", "shop-goods-import"],
        interactive_recovery=False,
    )


def import_jst_shop_goods(*, file_path: str | Path, shop_name: str, mode: str = "ignore") -> dict[str, Any]:
    return _run_ops(
        [
            "--json",
            "jst",
            "shop-goods",
            "import",
            "--file",
            str(file_path),
            "--shop-name",
            shop_name,
            "--mode",
            mode,
            "--output",
            "json",
        ]
    )
