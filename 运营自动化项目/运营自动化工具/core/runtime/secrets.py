"""通用敏感参数脱敏。

跨切面安全工具，不含任何业务逻辑：把 CLI 风格参数列表里跟在敏感 flag 后面的值
打码，避免验证码 / 密码 / token 等明文落进 run.json / step / 任务上下文 / 日志。

内存中的 ctx.inputs["args"] 仍保留真实值（step 需要用），只在序列化落盘时脱敏。
"""
from __future__ import annotations

from typing import Any

# 跟在这些 flag 之后的值会被打码。
SENSITIVE_ARG_FLAGS = frozenset(
    {
        "--code",
        "--password",
        "--passwd",
        "--token",
        "--secret",
        "--authorization",
        "--cookie",
        "--api-key",
        "--apikey",
    }
)

_MASK = "****"


def redact_cli_args(args: Any) -> Any:
    """把参数列表里敏感 flag 的值替换成掩码，返回新列表。

    支持 ``--code 1234`` 和 ``--code=1234`` 两种写法。非列表原样返回。
    """
    if not isinstance(args, (list, tuple)):
        return args
    redacted: list[Any] = []
    mask_next = False
    for item in args:
        if mask_next:
            redacted.append(_MASK)
            mask_next = False
            continue
        text = item if isinstance(item, str) else str(item)
        if text in SENSITIVE_ARG_FLAGS:
            redacted.append(text)
            mask_next = True
            continue
        if "=" in text and text.split("=", 1)[0] in SENSITIVE_ARG_FLAGS:
            redacted.append(f"{text.split('=', 1)[0]}={_MASK}")
            continue
        redacted.append(item)
    return redacted


def redact_inputs(inputs: Any) -> Any:
    """对 inputs 字典里的 ``args`` 字段做脱敏，返回浅拷贝。"""
    if not isinstance(inputs, dict):
        return inputs
    if "args" not in inputs:
        return inputs
    cleaned = dict(inputs)
    cleaned["args"] = redact_cli_args(inputs.get("args"))
    return cleaned
