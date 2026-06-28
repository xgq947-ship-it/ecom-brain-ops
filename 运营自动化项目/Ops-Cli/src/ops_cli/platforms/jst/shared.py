from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ops_cli.capabilities import current_capability_execution


SceneCheckFn = Callable[[dict[str, Any]], dict[str, Any]]
SceneReadFn = Callable[[Path], dict[str, Any]]
SceneRefreshFn = Callable[..., Any]


def surface_jst_login_if_needed(page: Any) -> None:
    """聚水潭 9222 自动化通用「登录失效」检测：

    若当前 9222 页面落在登录页（URL 命中 /login，或正文同时出现「登录」+「密码/账号」），
    则把专用 Chrome 切到前台让用户手动登录，并抛出统一错误中断流程。

    这是 9222 浏览器唯一允许主动弹窗的场景；其余自动化行为保持静默后台运行。
    调用方需已把 sessionhub_root 加入 sys.path（各 9222 流程在 import scene.chrome_cdp 时已完成）。
    """
    url = (getattr(page, "url", "") or "").lower()
    body = ""
    try:
        body = page.locator("body").inner_text(timeout=2000)
    except Exception:
        body = ""
    is_login = ("/login" in url) or ("登录" in body and ("密码" in body or "账号" in body))
    if not is_login:
        return

    reason = "JST_LOGIN_REQUIRED：检测到聚水潭登录页，已将专用 Chrome 切到前台，请先完成登录后重试。"
    # 兜底确保 sessionhub_root 在 sys.path 上（部分流程只连 9222、未显式 import scene.*）。
    try:
        import sys

        from ops_cli.config import get_config

        root = str(Path(get_config().sessionhub_root).expanduser().resolve())
        if root not in sys.path:
            sys.path.insert(0, root)
        from scene.chrome_cdp import surface_for_login  # type: ignore

        surface_for_login(reason)
    except ImportError:
        # 极端情况下 sessionhub 不可用，至少把清晰错误抛出去（不静默吞掉登录问题）。
        raise RuntimeError(
            "JST_LOGIN_REQUIRED：检测到聚水潭登录页，请先在 9222 专用 Chrome 完成登录后重试。"
        )


def ensure_scene_file_ready(
    *,
    scene_path: Path,
    read_scene: SceneReadFn,
    validate_scene: SceneCheckFn,
    refresh_scene: SceneRefreshFn,
    next_command: str,
    missing_label: str,
    invalid_label: str,
) -> dict[str, Any]:
    initial_exists = scene_path.exists()
    if initial_exists:
        try:
            existing_check = validate_scene(read_scene(scene_path))
            if existing_check.get("valid"):
                return existing_check
        except Exception:
            pass

    execution = current_capability_execution()
    if execution is not None and not execution.allow_recovery:
        execution.recovery.mark_required()
        raise RuntimeError(f"{invalid_label} 不可用，当前执行模式禁止自动登录恢复。请先运行 `{next_command}`。")
    refresh_scene(force=initial_exists)

    if not scene_path.exists():
        raise RuntimeError(f"未找到{missing_label}：{scene_path}。请先运行 `{next_command}`。")

    refreshed_check = validate_scene(read_scene(scene_path))
    if not refreshed_check.get("valid"):
        reason = refreshed_check.get("reason") or "scene 不可用"
        raise RuntimeError(f"{invalid_label} 不可用：{reason}。请先运行 `{next_command}`。")
    if execution is not None:
        execution.recovery.mark_refreshed(scene_path.stem)
    return refreshed_check
