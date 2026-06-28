import sys
from pathlib import Path
from typing import Any

from ops_cli.capabilities import current_capability_execution
from ops_cli.config import get_config


class SessionHubIntegrationError(RuntimeError):
    pass


class SessionHubSceneManager:
    def __init__(self, root: str | Path | None = None, *, wait_seconds: int = 90) -> None:
        configured = root or get_config().sessionhub_root
        self.root = Path(configured).expanduser().resolve()
        self.wait_seconds = wait_seconds

    def _ensure_import_path(self) -> Path:
        if not self.root.is_dir():
            raise FileNotFoundError(f"未找到 SessionHub 目录：{self.root}")
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        return self.root

    def capture_hint(self, site: str, scene: str) -> str:
        self._ensure_import_path()
        platform = "tmcs" if site == "tmall_chaoshi" else "jst"
        return (
            f"ops --json --interactive-login {platform} auth ensure "
            f"# scene={scene}；业务命令会按需捕获对应 scene"
        )

    def check_scene(self, site: str, scene: str) -> dict[str, Any]:
        self._ensure_import_path()
        from scene.session_check import check_session  # type: ignore

        return check_session(site, scene)

    def ensure_scene(
        self,
        site: str,
        scene: str,
        *,
        auto_capture: bool = True,
        wait_login: bool = True,
    ) -> dict[str, Any]:
        self._ensure_import_path()
        from scene.api import public_session  # type: ignore
        from scene.token_capture import CaptureError, capture_session  # type: ignore

        try:
            checked = self.check_scene(site, scene)
        except FileNotFoundError:
            checked = {"status": "invalid", "check_result": {"reason": "session 文件不存在"}}
        except Exception as exc:
            checked = {"status": "invalid", "check_result": {"reason": str(exc)}}

        if checked.get("status") == "valid":
            session = public_session(checked)
            session["source"] = "sessionhub"
            return session

        execution = current_capability_execution()
        if execution is not None and not execution.allow_recovery:
            execution.recovery.mark_required()
            auto_capture = False
        if not auto_capture:
            reason = (checked.get("check_result") or {}).get("reason") or "session 不可用"
            raise SessionHubIntegrationError(
                f"{site}/{scene} session 不可用：{reason}\n可执行：\n{self.capture_hint(site, scene)}"
            )
        if not wait_login:
            raise SessionHubIntegrationError(
                f"{site}/{scene} session 不可用，且 wait_login=False。\n可执行：\n{self.capture_hint(site, scene)}"
            )

        try:
            capture_session(site, scene, wait_seconds=self.wait_seconds)
            checked = self.check_scene(site, scene)
        except CaptureError as exc:
            raise SessionHubIntegrationError(str(exc)) from exc
        except Exception as exc:
            raise SessionHubIntegrationError(f"捕获或复检失败：{exc}") from exc

        if checked.get("status") != "valid":
            reason = (checked.get("check_result") or {}).get("reason") or "自动捕获后仍不可用"
            raise SessionHubIntegrationError(reason)

        session = public_session(checked)
        session["source"] = "sessionhub"
        if execution is not None:
            execution.recovery.mark_refreshed(scene)
        return session

    def capture_scene(self, site: str, scene: str) -> dict[str, Any]:
        self._ensure_import_path()
        from scene.api import public_session  # type: ignore
        from scene.token_capture import CaptureError, capture_session  # type: ignore

        execution = current_capability_execution()
        if execution is not None and not execution.allow_recovery:
            execution.recovery.mark_required()
            raise SessionHubIntegrationError(
                f"{site}/{scene} 需要交互捕获，当前执行模式禁止自动登录恢复。\n可执行：\n{self.capture_hint(site, scene)}"
            )
        try:
            capture_session(site, scene, wait_seconds=self.wait_seconds)
            checked = self.check_scene(site, scene)
        except CaptureError as exc:
            raise SessionHubIntegrationError(str(exc)) from exc
        except Exception as exc:
            raise SessionHubIntegrationError(f"捕获或复检失败：{exc}") from exc

        if checked.get("status") != "valid":
            reason = (checked.get("check_result") or {}).get("reason") or "自动捕获后仍不可用"
            raise SessionHubIntegrationError(reason)

        session = public_session(checked)
        session["source"] = "sessionhub"
        if execution is not None:
            execution.recovery.mark_refreshed(scene)
        return session


def get_scene_manager(root: str | Path | None = None, *, wait_seconds: int = 90) -> SessionHubSceneManager:
    return SessionHubSceneManager(root=root, wait_seconds=wait_seconds)
