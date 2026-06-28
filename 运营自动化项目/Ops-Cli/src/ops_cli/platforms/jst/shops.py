"""聚水潭店铺注册表 —— 店铺身份的单一数据源。

历史上店铺信息（店铺 ID / 店铺名）散落在 stats / profit / report / reimburse 等多个
文件里各写各的，无法统一切换。本模块把每个店铺的全部身份字段绑定成一条记录，各 JST
命令通过 ``--shop <key>`` 选择店铺：

- 不传 ``--shop``：使用默认店铺，行为与历史完全一致（零破坏）。
- 传 ``--shop subor``：切到对应店铺，同时拿到它的 shop_id 与 shop_name。

可通过 ``.env`` 的 ``JST_SHOPS`` 追加/覆盖店铺（JSON 数组），例如::

    JST_SHOPS=[{"key":"newshop","shop_id":"123","shop_name":"新店铺"}]
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from ops_cli.config import get_config


@dataclass(frozen=True)
class JstShop:
    """单个聚水潭店铺的身份。"""

    key: str
    shop_id: str
    shop_name: str
    is_default: bool = False


# 内置店铺。default 为启明工贸，与历史默认口径一致。
_BUILTIN_SHOPS: tuple[JstShop, ...] = (
    JstShop(key="qiming", shop_id="12633507", shop_name="（猫超）福安市启明工贸有限公司（肖国清）", is_default=True),
    JstShop(key="subor", shop_id="14696833", shop_name="苏泊尔迎众专卖店（曹林辉）"),
    JstShop(key="aux", shop_id="11574492", shop_name="奥克斯索隆专卖店（肖国清）"),
)


def _load_env_shops() -> tuple[JstShop, ...]:
    raw = (get_config().jst_shops or "").strip()
    if not raw:
        return ()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JST_SHOPS 不是合法 JSON：{exc}") from exc
    shops: list[JstShop] = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("key"):
            continue
        shops.append(
            JstShop(
                key=str(item["key"]).strip(),
                shop_id=str(item.get("shop_id") or "").strip(),
                shop_name=str(item.get("shop_name") or "").strip(),
                is_default=bool(item.get("is_default")),
            )
        )
    return tuple(shops)


@lru_cache(maxsize=1)
def _registry() -> tuple[JstShop, ...]:
    # 环境覆盖：同 key 的 env 店铺覆盖内置店铺，并保持顺序。
    merged: dict[str, JstShop] = {shop.key: shop for shop in _BUILTIN_SHOPS}
    for shop in _load_env_shops():
        merged[shop.key] = shop
    return tuple(merged.values())


def all_shops() -> tuple[JstShop, ...]:
    """返回全部已注册店铺。"""
    return _registry()


def default_shop() -> JstShop:
    """返回默认店铺（标了 is_default 的第一个，否则第一个）。"""
    shops = _registry()
    for shop in shops:
        if shop.is_default:
            return shop
    if not shops:
        raise RuntimeError("未注册任何聚水潭店铺。")
    return shops[0]


def resolve_shop(selector: str | None) -> JstShop:
    """把 ``--shop`` 选择器解析成店铺。

    selector 可以是 key / shop_id / shop_name；None 或空返回默认店铺。
    """
    if selector is None or not str(selector).strip():
        return default_shop()
    needle = str(selector).strip()
    for shop in _registry():
        if needle in (shop.key, shop.shop_id, shop.shop_name):
            return shop
    available = ", ".join(shop.key for shop in _registry())
    raise RuntimeError(f"未知店铺 --shop={needle!r}，可选：{available}")


def other_shop_ids(shop: JstShop | None = None) -> list[str]:
    """返回除指定店铺（默认=默认店铺）外其它已注册店铺的 shop_id 列表。

    供订单统计 learn 抓取时取消勾选其它店铺用。
    """
    target = shop or default_shop()
    return [s.shop_id for s in _registry() if s.shop_id and s.shop_id != target.shop_id]
