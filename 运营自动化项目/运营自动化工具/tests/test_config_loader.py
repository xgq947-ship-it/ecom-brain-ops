from __future__ import annotations

from pathlib import Path

import pytest

from core.config_loader import (
    PROJECT_ROOT,
    _KNOWN_KEYS,
    _parse_simple_yaml,
    get_path,
    load_paths,
)


def test_repo_relative_paths_derive_without_config() -> None:
    """仓库自身路径无需任何配置即可解析，且锚定在仓库位置。"""
    paths = load_paths(config_path=PROJECT_ROOT / "nonexistent.yaml")
    assert paths["runtime_dir"] == PROJECT_ROOT / "runtime"
    assert paths["logs_dir"] == PROJECT_ROOT / "logs"
    assert paths["ops_cli_root"] == PROJECT_ROOT.parent / "Ops-Cli"


def test_personal_paths_now_auto_derived() -> None:
    """以前必须写 paths.yaml 的「个人路径」现在零配置就能推导出来。"""
    paths = load_paths(config_path=PROJECT_ROOT / "nonexistent.yaml")
    assert paths["desktop_dir"] == Path.home() / "Desktop"
    assert paths["downloads_dir"] == Path.home() / "Downloads"
    assert paths["ecommerce_brain_dir"] == PROJECT_ROOT.parent.parent
    assert paths["reimbursement_dir"] == PROJECT_ROOT.parent / "刷单数据"
    assert paths["jst_product_master_file"].parent == PROJECT_ROOT.parent / "主数据"


def test_brain_root_env_override(monkeypatch) -> None:
    """ECOM_BRAIN_DIR 环境变量可改电商Brain 锚点。"""
    monkeypatch.setenv("ECOM_BRAIN_DIR", "/tmp/my_brain")
    expected = Path("/tmp/my_brain").resolve()  # macOS 会把 /tmp 规范化成 /private/tmp
    paths = load_paths(config_path=PROJECT_ROOT / "nonexistent.yaml")
    assert paths["ecommerce_brain_dir"] == expected
    assert paths["product_library_dir"] == expected / "01-产品库"


def test_per_key_env_override(monkeypatch) -> None:
    """OPS_PATH_<KEY> 环境变量优先级最高。"""
    monkeypatch.setenv("OPS_PATH_DESKTOP_DIR", "/tmp/desk")
    paths = load_paths(config_path=PROJECT_ROOT / "nonexistent.yaml")
    assert paths["desktop_dir"] == Path("/tmp/desk")


def test_nas_product_root_follows_mount_override(tmp_path: Path) -> None:
    """覆盖 company_nas_mount 后，未显式配置的 product_root 自动跟随。"""
    yaml_file = tmp_path / "paths.yaml"
    yaml_file.write_text("company_nas_mount: /Volumes/other\n", encoding="utf-8")
    paths = load_paths(config_path=yaml_file)
    assert paths["company_nas_mount"] == Path("/Volumes/other")
    assert paths["company_nas_product_root"] == Path("/Volumes/other/产品资料（运营）/1.产品资料")


def test_load_paths_merges_yaml_over_defaults(tmp_path: Path) -> None:
    """配置文件值覆盖推导默认值，其余默认仍在。"""
    yaml_file = tmp_path / "paths.yaml"
    yaml_file.write_text("runtime_dir: /tmp/custom_runtime\n", encoding="utf-8")
    paths = load_paths(config_path=yaml_file)
    assert paths["runtime_dir"] == Path("/tmp/custom_runtime")
    assert "logs_dir" in paths


def test_parse_simple_yaml_still_works(tmp_path: Path) -> None:
    """回归：扁平 YAML 解析器仍可用。"""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("key1: value1\nkey2: value2\n# comment\n", encoding="utf-8")
    assert _parse_simple_yaml(yaml_file) == {"key1": "value1", "key2": "value2"}


def test_known_keys_cover_consumers() -> None:
    """消费方用到的 key 都在已知 key 全集里。"""
    for key in ("desktop_dir", "downloads_dir", "wechat_file_dir", "company_nas_mount"):
        assert key in _KNOWN_KEYS


def test_get_path_unknown_raises() -> None:
    """拼错的未知 key 抛明确错误。"""
    with pytest.raises(KeyError, match="未知路径配置"):
        get_path("nonexistent_path_xyz")


def test_load_paths_does_not_probe_wechat_container(monkeypatch) -> None:
    """回归：普通路径解析绝不能触碰微信 TCC 容器（否则 launchd 下弹隐私框/卡死）。"""
    import core.config_loader as cfg

    calls: list[str] = []
    monkeypatch.setattr(cfg, "_discover_wechat_file_dir", lambda: calls.append("x") or None)
    cfg._wechat_cache = cfg._WECHAT_UNSET  # 清缓存
    cfg.load_paths(config_path=PROJECT_ROOT / "nonexistent.yaml")
    get_path("logs_dir")
    get_path("desktop_dir")
    assert calls == [], "load_paths/get_path(非微信) 不得探测微信容器"


def test_wechat_resolved_lazily_and_cached(monkeypatch) -> None:
    """只有取 wechat_file_dir 且无覆盖时才探测一次，并缓存。"""
    import core.config_loader as cfg

    calls: list[str] = []
    monkeypatch.setattr(cfg, "_discover_wechat_file_dir", lambda: (calls.append("x"), Path("/tmp/wx"))[1])
    monkeypatch.setattr(cfg, "_LEGACY_CONFIG", PROJECT_ROOT / "nonexistent.yaml")
    monkeypatch.setattr(cfg, "_LOCAL_CONFIG", PROJECT_ROOT / "nonexistent.yaml")
    cfg._wechat_cache = cfg._WECHAT_UNSET
    assert cfg.get_path("wechat_file_dir") == Path("/tmp/wx")
    assert cfg.get_path("wechat_file_dir") == Path("/tmp/wx")
    assert len(calls) == 1, "微信探测应只发生一次（缓存）"
