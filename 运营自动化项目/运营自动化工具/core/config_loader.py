from __future__ import annotations

import os
from pathlib import Path

from core.business_paths import apply_post_merge, business_paths


# ── 锚点 ────────────────────────────────────────────────────────────────────
# 所有路径都从下面三个锚点推导，而不是写死 /Users/<name>/... 的绝对路径，
# 这样换电脑 / 换用户名 / 别人 clone 都能零配置直接跑。
#   PROJECT_ROOT  = 运营自动化工具/           （由本文件位置推导，永远对）
#   STORE_ROOT    = 02-运营店铺/              （PROJECT_ROOT 的上级）
#   BRAIN_ROOT    = 电商Brain/                （STORE_ROOT 的上级）
#   HOME          = ~                         （Path.home()）
# 非标准布局可用环境变量覆盖锚点：ECOM_STORE_ROOT / ECOM_BRAIN_DIR。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATIC_STORE_ROOT = PROJECT_ROOT.parent

# 配置文件：committed 的只有 paths.yaml.example（模板）；
# paths.yaml（旧入口，兼容）与 paths.local.yaml（推荐）都 gitignore，仅作本机覆盖。
_LEGACY_CONFIG = PROJECT_ROOT / "config" / "paths.yaml"
_LOCAL_CONFIG = PROJECT_ROOT / "config" / "paths.local.yaml"

# glob 探测不到时需要用户在 paths.local.yaml 手填的 key（给出友好报错）。
_REQUIRES_LOCAL_CONFIG = {"wechat_file_dir"}


def _store_root() -> Path:
    env = os.environ.get("ECOM_STORE_ROOT")
    return Path(env).expanduser().resolve() if env else _STATIC_STORE_ROOT


def _brain_root() -> Path:
    env = os.environ.get("ECOM_BRAIN_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return _store_root().parent


def _discover_wechat_file_dir() -> Path | None:
    """自动发现微信文件目录（路径里含与账号绑定的 wxid，无法写死）。
    取 xwechat_files/wxid_*/msg/file 里最近修改的一个。找不到返回 None。

    ⚠️ 微信容器是 macOS TCC 保护的「其他 App 数据」，访问它会弹隐私授权框
    （launchd 下无 GUI 可点 → 会卡住/失败）。因此本函数【绝不能】在 _derived_paths /
    load_paths / import 阶段被急切调用——只在真正需要微信路径时（get_path("wechat_file_dir")
    且无配置覆盖）通过 _cached_wechat_file_dir() 惰性调用一次。不用微信的 workflow 永不触碰它。"""
    base = Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    if not base.is_dir():
        return None
    candidates = [p for p in base.glob("wxid_*/msg/file") if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


_WECHAT_UNSET = object()
_wechat_cache: object = _WECHAT_UNSET


def _cached_wechat_file_dir() -> Path | None:
    """惰性 + 单次缓存地探测微信目录，避免每次路径解析都触碰 TCC 容器。"""
    global _wechat_cache
    if _wechat_cache is _WECHAT_UNSET:
        _wechat_cache = _discover_wechat_file_dir()
    return _wechat_cache  # type: ignore[return-value]


def _derived_paths() -> dict[str, Path]:
    """从锚点推导出的全部默认路径，无需任何配置文件即可工作。

    分两部分：
    - 框架通用路径（与具体业务无关，随框架走）在此就地定义；
    - 业务路径表（具体文件 / 目录命名）来自 `core/business_paths.py`，可整体替换。
    微信目录【不在这里探测】——它是 TCC 保护容器，急切探测会让每个 workflow 都弹隐私框；
    改为 get_path("wechat_file_dir") 惰性解析（见 _cached_wechat_file_dir / get_path）。
    """
    project = PROJECT_ROOT
    store = _store_root()
    brain = _brain_root()
    home = Path.home()
    runtime = project / "runtime"

    # 框架通用路径（不含任何具体业务命名）。
    paths: dict[str, Path] = {
        "project_root": project,
        "runtime_dir": runtime,
        "logs_dir": project / "logs",
        "ops_cli_root": store / "Ops-Cli",
        "ops_cli_bin": store / "Ops-Cli" / ".venv" / "bin" / "ops",
        "desktop_dir": home / "Desktop",
        "downloads_dir": home / "Downloads",
    }
    # 业务路径表（可整体替换，见 business_paths.py）。
    paths.update(business_paths(project=project, store=store, brain=brain, home=home))
    return paths


# 已知 key 全集（用于区分「需配置」与「拼错的未知 key」）。
_KNOWN_KEYS = set(_derived_paths().keys()) | _REQUIRES_LOCAL_CONFIG

# 兼容旧导出：DEFAULT_PATHS 现在等价于「推导默认值」的快照。
DEFAULT_PATHS = _derived_paths()


def _parse_simple_yaml(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and value:
            values[key] = value
    return values


def load_paths(config_path: Path | None = None) -> dict[str, Path]:
    """按优先级合并路径（低→高）：
    推导默认值  <  paths.yaml（旧）  <  paths.local.yaml（推荐）  <  OPS_PATH_* 环境变量。
    显式传 config_path 时只用该文件作为唯一覆盖（供测试）。"""
    merged: dict[str, Path] = dict(_derived_paths())
    explicit: set[str] = set()

    files = [config_path] if config_path is not None else [_LEGACY_CONFIG, _LOCAL_CONFIG]
    for cfg in files:
        if not cfg or not cfg.exists():
            continue
        try:
            for key, value in _parse_simple_yaml(cfg).items():
                merged[key] = Path(value).expanduser()
                explicit.add(key)
        except OSError:
            continue

    # 单 key 环境变量覆盖：OPS_PATH_<KEY 大写>，方便 CI / 临时改路径。
    for key in list(_KNOWN_KEYS):
        env_value = os.environ.get("OPS_PATH_" + key.upper())
        if env_value:
            merged[key] = Path(env_value).expanduser()
            explicit.add(key)

    # 合并完成后的业务联动（如 NAS 产品根目录跟随最终挂载点），交给业务表处理。
    apply_post_merge(merged, explicit)

    return {key: value.expanduser() for key, value in merged.items()}


def get_path(name: str) -> Path:
    paths = load_paths()
    if name in paths:
        return paths[name]
    # 微信目录惰性解析：配置覆盖里没有时，才（且仅在此处）探测一次 TCC 容器。
    # 不用微信的 workflow 永远走不到这里，不会触发隐私授权框。
    if name == "wechat_file_dir":
        discovered = _cached_wechat_file_dir()
        if discovered is not None:
            return discovered
    if name in _KNOWN_KEYS:
        raise KeyError(
            f"路径配置缺失：{name}\n"
            f"该路径无法自动推导（如微信目录未探测到）。请运行 "
            f"`python3 run.py setup` 自动生成，或在 config/paths.local.yaml 手动配置。"
        )
    raise KeyError(f"未知路径配置：{name}")
