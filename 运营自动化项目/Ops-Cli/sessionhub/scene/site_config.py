from __future__ import annotations

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = ROOT / "config" / "sites"


class ConfigError(RuntimeError):
    pass


def _apply_scene_defaults(data: dict[str, Any]) -> dict[str, Any]:
    scenes = data.get("scenes") or {}
    for raw_scene in scenes.values():
        if not isinstance(raw_scene, dict):
            continue
        raw_scene.setdefault("auto_actions", [])
        raw_scene.setdefault("wait_seconds", 90)
        raw_scene.setdefault("capture_retry_limit", 2)
        raw_scene.setdefault("sensitive_artifact_policy", "local_ignored")
    return data


def load_site_config(site: str) -> dict[str, Any]:
    """从 config/sites/<site>.yaml 加载站点配置。

    站点配置（URL / 场景 / 动作）的唯一来源是 yaml 文件，本加载器与具体平台无关——
    新增平台只需放一个 config/sites/<新平台>.yaml，无需改动本文件。
    """
    path = CONFIG_ROOT / f"{site}.yaml"
    if not path.exists():
        raise ConfigError(f"找不到站点配置：{path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "缺少 PyYAML，无法解析站点配置；请先安装依赖：pip install -r requirements.txt"
        ) from exc
    data = yaml.safe_load(text) or {}
    if not data.get("site"):
        raise ConfigError(f"配置文件缺少 site：{path}")
    return _apply_scene_defaults(data)


def get_scene_config(site: str, scene: str) -> dict[str, Any]:
    config = load_site_config(site)
    scenes = config.get("scenes") or {}
    if scene not in scenes:
        raise ConfigError(f"{site} 未配置场景：{scene}")
    return scenes[scene]


def target_url_for(config: dict[str, Any], scene_config: dict[str, Any] | None = None) -> str:
    if scene_config:
        url = (scene_config.get("target_url") or "").strip()
        if url:
            return url
    for key in ("download_manage_url", "home_url", "login_url"):
        url = (config.get(key) or "").strip()
        if url:
            return url
    site_name = config.get("site", "<site>")
    raise ConfigError(
        f"站点 URL 还没有配置。请先编辑 config/sites/{site_name}.yaml，"
        "补充 download_manage_url 或 home_url/login_url。"
    )
