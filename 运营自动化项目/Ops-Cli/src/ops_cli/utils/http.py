from typing import Any

import httpx

# 强制排除 brotli（br）压缩：brotlicffi 的流式解压器在处理较大响应时存在 bug，
# 会报 "decoder process called with data when 'can_accept_more_data()' is False"。
# 小响应（如 qiming/aux 店铺）没问题，但 subor 店铺 ~600KB 的聚水潭利润响应会触发。
# 由于 httpx 在装了 brotlicffi 时默认 accept-encoding 就带 br，且各平台模块对请求头的
# 处理不一（有的透传 scene 原始头、有的剥掉后用 httpx 默认），逐个修既易漏又会被忘。
# 在 build_client 这个唯一入口用 request hook 在请求发出前统一覆盖 accept-encoding，
# 任何 per-request / scene 头都盖不过它，全平台层一处生效、新代码自动免疫。
SAFE_ACCEPT_ENCODING = "gzip, deflate"


def _force_safe_accept_encoding(request: httpx.Request) -> None:
    request.headers["accept-encoding"] = SAFE_ACCEPT_ENCODING


def build_client(**kwargs: Any) -> httpx.Client:
    event_hooks = dict(kwargs.pop("event_hooks", {}) or {})
    request_hooks = list(event_hooks.get("request", []))
    request_hooks.insert(0, _force_safe_accept_encoding)
    event_hooks["request"] = request_hooks
    return httpx.Client(
        timeout=kwargs.pop("timeout", 10.0),
        event_hooks=event_hooks,
        **kwargs,
    )
