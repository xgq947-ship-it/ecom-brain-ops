"""聚水潭店铺注册表测试。"""
from __future__ import annotations

import pytest

from ops_cli.platforms.jst import shops


def test_default_shop_is_qiming() -> None:
    default = shops.default_shop()
    assert default.key == "qiming"
    assert default.shop_id == "12633507"
    assert default.is_default is True


def test_resolve_by_key_id_and_name() -> None:
    assert shops.resolve_shop("subor").shop_id == "14696833"
    assert shops.resolve_shop("11574492").key == "aux"
    assert shops.resolve_shop("苏泊尔迎众专卖店（曹林辉）").key == "subor"


def test_resolve_none_returns_default() -> None:
    assert shops.resolve_shop(None).key == shops.default_shop().key
    assert shops.resolve_shop("  ").key == shops.default_shop().key


def test_resolve_unknown_raises() -> None:
    with pytest.raises(RuntimeError, match="未知店铺"):
        shops.resolve_shop("does-not-exist")


def test_other_shop_ids_excludes_default_and_dropped_shop() -> None:
    others = shops.other_shop_ids()
    assert shops.default_shop().shop_id not in others
    assert set(others) == {"14696833", "11574492"}
    # 16684542 已从注册表移除，不应再出现
    assert "16684542" not in others


def test_env_override_appends_shop(monkeypatch) -> None:
    import json

    from ops_cli import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: config.AppConfig(
            jst_shops=json.dumps([{"key": "newshop", "shop_id": "999", "shop_name": "新店铺"}])
        ),
    )
    monkeypatch.setattr(shops, "get_config", config.get_config)
    shops._registry.cache_clear()
    try:
        assert shops.resolve_shop("newshop").shop_id == "999"
    finally:
        shops._registry.cache_clear()
