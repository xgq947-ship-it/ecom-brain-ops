from pathlib import Path

from ops_cli.config import get_config


def test_master_data_defaults_point_to_workspace_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    get_config.cache_clear()

    config = get_config()

    master = Path.home() / "Desktop" / "电商Brain" / "02-运营店铺" / "主数据"
    assert config.jst_product_source_path == str(master / "聚水潭商品资料（最新）.xlsx")
    assert config.tmcs_product_latest_path == str(master / "猫超商品列表导出 (最新）.xlsx")

    get_config.cache_clear()
