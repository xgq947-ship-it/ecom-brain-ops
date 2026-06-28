from __future__ import annotations

from pathlib import Path
from typing import Any

from notifier.hermes import HermesNotifier, HermesWeixinSender


class HermesWeChatNotifier:
    """Thin adapter over the verified local Hermes Weixin message tool."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        base_url: str | None = None,
        token: str | None = None,
        receiver: str | None = None,
        timeout: int = 10,
        agent_root: Path | None = None,
        env_path: Path | None = None,
        python_bin: Path | None = None,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url
        self.token = token
        self.receiver = receiver
        self.timeout = timeout
        self._notifier = HermesNotifier(
            enabled=enabled,
            timeout=timeout,
            agent_root=agent_root,
            env_path=env_path,
            python_bin=python_bin,
        )
        self._sender = HermesWeixinSender(notifier=self._notifier)

    @classmethod
    def from_config(cls, config: dict[str, Any], *, force_enabled: bool = False) -> "HermesWeChatNotifier":
        notifier = HermesNotifier.from_config(config, force_enabled=force_enabled)
        return cls(
            enabled=notifier.enabled,
            base_url=None,
            token=None,
            receiver=None,
            timeout=notifier.timeout,
            agent_root=notifier.agent_root,
            env_path=notifier.env_path,
            python_bin=notifier.python_bin,
        )

    def send_text(self, title: str, content: str, dry_run: bool = False) -> dict[str, Any]:
        message = f"{title}\n{content}".strip()
        if dry_run:
            return {"success": True, "sent": False, "dry_run": True, "preview": message}
        if not self.enabled:
            return {"success": True, "sent": False, "dry_run": False, "reason": "Hermes 微信通知未启用"}
        try:
            result = self._sender.send(message)
            return {"success": True, "sent": True, "dry_run": False, "result": result}
        except Exception as exc:
            return {"success": False, "sent": False, "dry_run": False, "error": str(exc)}


def send_hermes_wechat_message(
    title: str,
    content: str,
    receiver: str | None = None,
    dry_run: bool = False,
) -> bool:
    notifier = HermesWeChatNotifier.from_config({}, force_enabled=not dry_run)
    notifier.receiver = receiver or notifier.receiver
    return bool(notifier.send_text(title, content, dry_run=dry_run).get("success"))
