from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


_TRUTHY = {"1", "true", "yes", "on"}


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class HermesNotifier:
    """Generic Hermes-backed notifier for Weixin / Feishu / WeCom."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        timeout: int = 10,
        agent_root: Path | None = None,
        env_path: Path | None = None,
        python_bin: Path | None = None,
        hermes_home: Path | None = None,
        hermes_bin: Path | None = None,
        weixin_target: str = "weixin",
        default_feishu_target: str | None = None,
        default_wecom_key: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.timeout = timeout
        self.agent_root = agent_root or Path(os.getenv("HERMES_AGENT_ROOT", Path.home() / ".hermes" / "hermes-agent"))
        self.env_path = env_path or Path(os.getenv("HERMES_ENV_PATH", Path.home() / ".hermes" / ".env"))
        configured_python = os.getenv("HERMES_PYTHON_BIN", "").strip()
        self.python_bin = python_bin or (Path(configured_python) if configured_python else self.agent_root / "venv" / "bin" / "python3")
        self.hermes_home = hermes_home or Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))
        configured_bin = os.getenv("HERMES_BIN", "").strip()
        self.hermes_bin = hermes_bin or (Path(configured_bin) if configured_bin else Path.home() / ".local" / "bin" / "hermes")
        self.default_feishu_target = default_feishu_target or os.getenv(
            "HERMES_FEISHU_TARGET",
            "feishu:oc_eb4b4846c2b7d10df1099e5aa75328a3",
        )
        self.default_wecom_key = default_wecom_key or os.getenv("HERMES_WECOM_KEY") or None
        self.weixin_target = os.getenv("HERMES_WEIXIN_TARGET", weixin_target)

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None, *, force_enabled: bool = False) -> "HermesNotifier":
        config = config or {}
        enabled = str(config.get("enabled", False)).lower() in _TRUTHY
        enabled_value = os.getenv("HERMES_NOTIFIER_ENABLED", "").strip()
        if enabled_value:
            enabled = enabled_value.lower() in _TRUTHY
        return cls(
            enabled=enabled or force_enabled,
            timeout=int(config.get("timeout_seconds", 10)),
            weixin_target=str(config.get("weixin_target") or "weixin"),
            default_feishu_target=str(config.get("default_feishu_target") or "") or None,
            default_wecom_key=str(config.get("default_wecom_key") or "") or None,
        )

    def send(
        self,
        content: str,
        *,
        dry_run: bool = False,
        msgtype: str = "text",
        target: str | None = None,
        key: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        message = f"{title}\n{content}".strip() if title else content
        if not message:
            return {"success": True, "sent": False, "reason": "无通知内容"}
        resolved_target, resolved_key = self._resolve_destination(target=target, key=key)
        if dry_run:
            return {
                "success": True,
                "sent": False,
                "dry_run": True,
                "preview": message,
                "target": resolved_target,
                "key": resolved_key,
            }
        if not self.enabled:
            return {"success": True, "sent": False, "dry_run": False, "reason": "Hermes 通知未启用"}
        try:
            load_env(self.env_path)
            self._validate_runtime()
            if resolved_target == "wecom":
                result = self._send_wecom(message, msgtype=msgtype, key=resolved_key)
            else:
                hermes_target = self._resolve_hermes_target(resolved_target)
                result = self._send_message_tool(hermes_target, message)
            return {
                "success": True,
                "sent": True,
                "dry_run": False,
                "target": resolved_target,
                "key": resolved_key,
                "result": result,
            }
        except Exception as exc:
            return {
                "success": False,
                "sent": False,
                "dry_run": False,
                "target": resolved_target,
                "key": resolved_key,
                "error": str(exc),
            }

    def _resolve_destination(self, *, target: str | None, key: str | None) -> tuple[str, str | None]:
        if key:
            return "wecom", key
        raw_target = (target or "").strip()
        if not raw_target:
            return "wecom", self.default_wecom_key
        if raw_target.startswith("wecom:"):
            return "wecom", raw_target.split(":", 1)[1].strip() or self.default_wecom_key
        if raw_target in {"wecom", "weixin", "feishu"}:
            return raw_target, self.default_wecom_key if raw_target == "wecom" else None
        if raw_target.startswith("feishu:") or raw_target.startswith("weixin:"):
            channel = raw_target.split(":", 1)[0]
            return channel, None
        raise ValueError(f"不支持的通知目标：{raw_target}")

    def _resolve_hermes_target(self, target: str) -> str:
        if target == "weixin":
            return self.weixin_target
        if target == "feishu":
            return self.default_feishu_target
        return target

    def _validate_runtime(self) -> None:
        if not self.agent_root.exists():
            raise RuntimeError(f"Hermes agent 目录不存在：{self.agent_root}")
        if not self.python_bin.exists():
            raise RuntimeError(f"Hermes Python 不存在：{self.python_bin}")

    def _send_message_tool(self, target: str, message: str) -> dict[str, Any]:
        if not target:
            raise RuntimeError("Hermes 目标为空")
        script = (
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from tools.send_message_tool import send_message_tool\n"
            "result = send_message_tool({'target': sys.argv[2], 'message': sys.stdin.read()})\n"
            "print(result)\n"
        )
        completed = subprocess.run(
            [str(self.python_bin), "-c", script, str(self.agent_root), target],
            cwd=str(self.agent_root),
            env=os.environ.copy(),
            input=message,
            text=True,
            capture_output=True,
            timeout=max(self.timeout, 60),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "Hermes 消息发送失败")
        raw = completed.stdout.strip().splitlines()[-1]
        result = json.loads(raw)
        if not result.get("success"):
            raise RuntimeError(str(result.get("error") or raw))
        return result

    def _send_wecom(self, message: str, *, msgtype: str, key: str | None) -> dict[str, Any]:
        send_wecom = self._load_send_wecom()
        sig = inspect.signature(send_wecom)
        kwargs: dict[str, Any] = {"msgtype": msgtype}
        if "key" in sig.parameters and key is not None:
            kwargs["key"] = key
        return send_wecom(message, **kwargs)

    def _load_send_wecom(self):
        scripts_dir = self.hermes_home / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from send_wecom import send_wecom  # noqa: E402

        return send_wecom


class HermesWeixinSender:
    """Weixin sender with retry handling for Hermes context token issues."""

    def __init__(self, *, notifier: HermesNotifier | None = None) -> None:
        self.notifier = notifier or HermesNotifier.from_config({}, force_enabled=True)

    def send(self, message: str) -> dict[str, Any]:
        load_env(self.notifier.env_path)
        return self._send_with_retry(message)

    def _send_with_retry(self, message: str) -> dict[str, Any]:
        try:
            return self._send_once(message)
        except RuntimeError as exc:
            error = str(exc)
            should_refresh = "ret=-2" in error or "errcode=-14" in error or "context_token" in error
            if not should_refresh:
                raise
            self._clear_home_context_token()
            self._restart_gateway()
            time.sleep(8)
            return self._send_once(message)

    def _send_once(self, message: str) -> dict[str, Any]:
        result = self.notifier._send_message_tool(self.notifier.weixin_target, message)
        if not result.get("success"):
            raise RuntimeError(result.get("error") or json.dumps(result, ensure_ascii=False))
        return result

    def _clear_home_context_token(self) -> bool:
        account_id = os.getenv("WEIXIN_ACCOUNT_ID", "").strip()
        home_channel = os.getenv("WEIXIN_HOME_CHANNEL", "").strip()
        if not account_id or not home_channel:
            return False
        path = self.notifier.hermes_home / "weixin" / "accounts" / f"{account_id}.context-tokens.json"
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        if home_channel not in data:
            return False
        data.pop(home_channel, None)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True

    def _restart_gateway(self) -> None:
        if not self.notifier.hermes_bin.exists():
            return
        subprocess.run(
            [str(self.notifier.hermes_bin), "gateway", "restart"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
