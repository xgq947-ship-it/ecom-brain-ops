from dataclasses import dataclass
from typing import Any

from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.output import CommandResponse

AUTH_RECOVERY_MARKERS = (
    "401",
    "unauthorized",
    "forbidden",
    "登录",
    "session",
    "cookie",
    "token",
    "验证码",
    "验证身份",
    "短信验证",
)


@dataclass(frozen=True)
class AuthTarget:
    platform: str
    site: str
    scene: str


def _response(command: str, target: AuthTarget, data: dict[str, Any]) -> CommandResponse:
    return CommandResponse(
        success=True,
        platform=target.platform,
        command=command,
        data=data,
    )


def is_probable_auth_error(error: object) -> bool:
    text = str(error).strip().lower()
    if not text:
        return False
    return any(marker in text for marker in AUTH_RECOVERY_MARKERS)


def check_auth_target(target: AuthTarget) -> CommandResponse:
    checked = get_scene_manager().check_scene(target.site, target.scene)
    check_result = checked.get("check_result") or {}
    return _response(
        "auth check",
        target,
        {
            "site": target.site,
            "scene": target.scene,
            "status": checked.get("status", "unknown"),
            "reason": check_result.get("reason", ""),
            "status_code": check_result.get("status_code"),
            "source": "sessionhub",
            "action": "check",
        },
    )


def ensure_auth_target(target: AuthTarget) -> CommandResponse:
    session = get_scene_manager().ensure_scene(target.site, target.scene)
    return _response(
        "auth ensure",
        target,
        {
            "site": target.site,
            "scene": target.scene,
            "status": "valid",
            "reason": "已确认 session 可用",
            "source": session.get("source", "sessionhub"),
            "action": "ensure",
            "url": session.get("url"),
            "method": session.get("method"),
        },
    )


def capture_auth_target(target: AuthTarget) -> CommandResponse:
    session = get_scene_manager().capture_scene(target.site, target.scene)
    return _response(
        "auth capture",
        target,
        {
            "site": target.site,
            "scene": target.scene,
            "status": "valid",
            "reason": "已重新捕获并通过校验",
            "source": session.get("source", "sessionhub"),
            "action": "capture",
            "url": session.get("url"),
            "method": session.get("method"),
        },
    )
