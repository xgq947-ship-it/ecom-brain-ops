from ops_cli.utils.http import SAFE_ACCEPT_ENCODING, build_client


def _run_request_hooks(client, request) -> None:
    for hook in client._event_hooks["request"]:
        hook(request)


def test_build_client_forces_safe_accept_encoding_overriding_per_request() -> None:
    """request hook 必须在发出前把 accept-encoding 改成不带 br 的安全值，
    哪怕调用方在 per-request 头里硬塞了 br，也要被盖掉。"""
    with build_client() as client:
        request = client.build_request(
            "POST",
            "https://example.com/api",
            headers={"accept-encoding": "gzip, deflate, br, zstd"},
        )
        _run_request_hooks(client, request)
        assert request.headers["accept-encoding"] == SAFE_ACCEPT_ENCODING
        assert "br" not in request.headers["accept-encoding"]


def test_build_client_sets_safe_accept_encoding_when_caller_omits_it() -> None:
    """调用方不传 accept-encoding 时，httpx 默认会带 br；hook 同样要兜住。"""
    with build_client() as client:
        request = client.build_request("GET", "https://example.com/api")
        _run_request_hooks(client, request)
        assert request.headers["accept-encoding"] == SAFE_ACCEPT_ENCODING


def test_build_client_preserves_caller_supplied_event_hooks() -> None:
    """不能吞掉调用方自己注册的 request hook。"""
    seen: list[str] = []

    def custom_hook(request) -> None:  # noqa: ANN001
        seen.append(str(request.url))

    with build_client(event_hooks={"request": [custom_hook]}) as client:
        request = client.build_request("GET", "https://example.com/api")
        _run_request_hooks(client, request)
        assert request.headers["accept-encoding"] == SAFE_ACCEPT_ENCODING
        assert seen == ["https://example.com/api"]
