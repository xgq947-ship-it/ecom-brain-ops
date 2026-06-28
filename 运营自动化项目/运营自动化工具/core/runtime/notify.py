"""统一通知入口，供各 workflow 复用（提醒 / 失败告警）。

把「dry-run 只产预览、不发送；真实执行才推送」这条安全语义收敛到一处，避免每个 workflow
各写一遍。具体通过哪个渠道发送由「通知后端注册表」决定（默认参考后端见 notify_backends.py）。

约定返回结构：
- 无内容：{"success": True, "sent": False, "reason": "无通知内容"}
- dry-run：{"success": True, "sent": False, "dry_run": True, "preview": content}
- 真实发送：{"sent": True, **底层发送结果}
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

# 通知后端注册表 —— 后端是「基础设施 / 渠道实现」，不属于引擎核心。
# 引擎只认「名字 → 返回 sender 可调用对象的工厂」这层接口；
# 下游 clone 框架后可用 register_sender() 注册自己的渠道（钉钉 / 邮件 / Server酱…），
# 无需改动本文件。默认参考后端（wecom / hermes）在 notify_backends.py 注册。
_SenderFactory = Callable[[], Callable[..., Any]]
_SENDER_FACTORIES: dict[str, _SenderFactory] = {}

# 默认路由用的后端名（仅是注册表键名，可按自己注册的后端改）。
DEFAULT_SENDER = "wecom"     # 未指定 target 时
TARGETED_SENDER = "hermes"   # 指定 target 时


def register_sender(name: str, factory: _SenderFactory) -> None:
    """注册 / 覆盖一个具名通知后端。"""
    _SENDER_FACTORIES[name] = factory


def _resolve_sender(target: str | None) -> Callable[..., Any]:
    """按 target 选后端：精确注册名优先，否则用默认路由（有 target→TARGETED，无→DEFAULT）。"""
    if target is not None and target in _SENDER_FACTORIES:
        return _SENDER_FACTORIES[target]()
    name = TARGETED_SENDER if target is not None else DEFAULT_SENDER
    factory = _SENDER_FACTORIES.get(name)
    if factory is None:
        raise RuntimeError(f"未注册的通知后端：{name}")
    return factory()


def send_notification(
    content: str,
    *,
    dry_run: bool,
    msgtype: str = "markdown",
    key: str | None = None,
    target: str | None = None,
    sender: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """发送通知；默认企业微信群是「仓库对接群」，dry-run 下绝不真实推送。"""
    if not content:
        return {"success": True, "sent": False, "reason": "无通知内容"}
    if dry_run:
        return {
            "success": True,
            "sent": False,
            "dry_run": True,
            "preview": content,
            "target": target or ("wecom" if key is None else "wecom"),
            "key": key,
        }
    send = sender or _resolve_sender(target)
    # 自动适配 sender 是否支持 key 参数
    sig = inspect.signature(send)
    kwargs: dict[str, Any] = {"msgtype": msgtype}
    if "key" in sig.parameters and key is not None:
        kwargs["key"] = key
    if "target" in sig.parameters and target is not None:
        kwargs["target"] = target
    result = send(content, **kwargs)
    if isinstance(result, dict):
        return {"sent": True, **result}
    return {"success": True, "sent": True}


# 加载并注册默认参考后端（wecom / hermes）。下游可在此之后用 register_sender 覆盖。
from core.runtime import notify_backends as _notify_backends  # noqa: E402,F401
