"""默认通知后端（参考实现）—— wecom（企业微信）/ hermes（微信·飞书·企业微信 agent）。

这两个是随框架附带的「参考后端」，import 时通过 register_sender 注册进 notify 的后端注册表。
别人 clone 框架后，可保留、删除或替换为自己的渠道（钉钉 / 邮件 / Server酱…）：
只要 `register_sender("<名字>", <返回 sender 的工厂>)`，无需改动通知引擎 notify.py。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

from core.runtime.notify import register_sender

_HERMES_SCRIPTS = Path.home() / ".hermes" / "scripts"


def _make_wecom_sender() -> Callable[..., Any]:
    if str(_HERMES_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_HERMES_SCRIPTS))
    from send_wecom import send_wecom  # noqa: E402

    return send_wecom


def _make_hermes_sender() -> Callable[..., Any]:
    from notifier.hermes import HermesNotifier

    notifier = HermesNotifier.from_config({}, force_enabled=True)
    return notifier.send


register_sender("wecom", _make_wecom_sender)
register_sender("hermes", _make_hermes_sender)
