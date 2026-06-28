from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ops_cli.capabilities import mark_scene_refreshed
from ops_cli.capabilities import require_interactive_recovery
from ops_cli.platforms.tmcs.inventory import DEFAULT_WAREHOUSE_CODE
from ops_cli.platforms.tmcs.inventory import _flatten_row
from ops_cli.platforms.tmcs.inventory import _search_inventory_rows
from ops_cli.platforms.tmcs.inventory import learn_inventory_adjust
from ops_cli.platforms.tmcs.shared import TMCS_INVENTORY_SEARCH_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_SITE
from ops_cli.platforms.tmcs.shared import check_scene_or_fail
from ops_cli.platforms.tmcs.shared import is_probable_auth_error
from ops_cli.platforms.tmcs.shared import load_scene_or_fail


FIELD_CANDIDATES = {
    "platform_item_id": [
        "platform_item_id",
        "platformItemId",
        "itemId",
        "item_id",
        "downItemId",
        "down_item_id",
    ],
    "platform_sku_id": [
        "platform_sku_id",
        "platformSkuId",
        "skuId",
        "sku_id",
        "downSkuId",
        "down_sku_id",
    ],
    "supplier_goods_id": [
        "supplier_goods_id",
        "supplierGoodsId",
        "supplierScItemId",
        "supplier_sc_item_id",
        "supplierItemId",
        "supplier_item_id",
        "supplierGoodsCode",
        "supplier_goods_code",
        "scItemId",
        "sc_item_id",
        "goodsId",
        "goods_id",
        "cargoId",
        "cargo_id",
        "vendorGoodsId",
        "vendor_goods_id",
    ],
    "merchant_goods_code": [
        "barCode",
        "bar_code",
        "merchant_goods_code",
        "merchantGoodsCode",
        "merchantSkuCode",
        "merchant_sku_code",
        "outerId",
        "outer_id",
        "goodsCode",
        "goods_code",
        "itemCode",
        "item_code",
        "skuCode",
        "sku_code",
        "erpCode",
        "erp_code",
        "scItemCode",
        "sc_item_code",
    ],
}


def normalize_item_ids(item_ids: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(item_ids, str):
        raw_items = item_ids.split(",")
    else:
        raw_items = list(item_ids)
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in raw_items:
        item_id = _to_text(raw).strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        normalized.append(item_id)
    return normalized


def _load_stock_template() -> dict[str, Any]:
    return {
        "inventory_search": load_scene_or_fail(
            TMCS_SITE,
            TMCS_INVENTORY_SEARCH_SCENE,
            next_command="ops tmcs inventory adjust-learn",
        )
    }


def _key_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value).strip()


def _pick(row: dict[str, Any], candidates: list[str]) -> str:
    flattened = _flatten_row(row)
    by_token = {_key_token(str(key).split(".")[-1]): value for key, value in flattened.items()}
    for candidate in candidates:
        value = by_token.get(_key_token(candidate))
        text = _to_text(value)
        if text:
            return text
    return ""


def standardize_stock_row(row: dict[str, Any]) -> dict[str, str]:
    return {
        field: _pick(row, candidates)
        for field, candidates in FIELD_CANDIDATES.items()
    }


def query_stock(*, item_ids: str | list[str], warehouse_code: str = DEFAULT_WAREHOUSE_CODE) -> list[dict[str, str]]:
    normalized_item_ids = normalize_item_ids(item_ids)
    if not normalized_item_ids:
        raise RuntimeError("请传入至少一个平台商品ID。")

    retried_for_auth = False
    while True:
        template = _load_stock_template()
        search_scene = template.get("inventory_search") or {}

        try:
            # scene 失效检测放进 try：失效错误（命中 auth marker）会落到下方 except，
            # 触发 learn_inventory_adjust 的 9222 自动重沉淀，而不是直接中断。
            check_scene_or_fail(TMCS_SITE, TMCS_INVENTORY_SEARCH_SCENE, next_command="ops tmcs inventory adjust-learn")
            rows: list[dict[str, str]] = []
            for item_id in normalized_item_ids:
                found_rows = _search_inventory_rows(search_scene=search_scene, warehouse_code=warehouse_code, item_id=item_id)
                for raw_row in found_rows:
                    standardized = standardize_stock_row(raw_row)
                    if not standardized["platform_item_id"]:
                        standardized["platform_item_id"] = item_id
                    rows.append(standardized)
            return rows
        except RuntimeError as exc:
            if retried_for_auth or not is_probable_auth_error(exc):
                raise
            require_interactive_recovery(TMCS_INVENTORY_SEARCH_SCENE)
            learn_inventory_adjust(force=True)
            mark_scene_refreshed(TMCS_INVENTORY_SEARCH_SCENE)
            retried_for_auth = True
