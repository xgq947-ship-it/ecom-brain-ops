"""JST 短信验证码弹窗检测/提交能力测试。

不连接真实 9222 Chrome：通过 monkeypatch 注入假 page/frame/context，验证：
- detect / submit 返回 JSON 结构正确；
- 验证码明文绝不出现在返回数据里，只暴露 masked_code；
- 校验、execute 守卫、各错误码语义正确。
"""
from __future__ import annotations

import json

import pytest

from ops_cli.platforms.jst import sms_verification as sv


class _FakeLocator:
    def __init__(self, *, visible: bool = False, count: int = 0):
        self._visible = visible
        self._count = count
        self.filled: list[str] = []
        self.clicked = 0

    def count(self) -> int:  # noqa: D401 - mimic playwright API
        return self._count

    @property
    def first(self) -> "_FakeLocator":
        return self

    def is_visible(self, timeout: int = 0) -> bool:
        return self._visible

    def inner_text(self, timeout: int = 0) -> str:
        return ""

    def fill(self, value: str, timeout: int = 0) -> None:
        self.filled.append(value)

    def click(self, timeout: int = 0) -> None:
        self.clicked += 1


class _FakeLocatorGroup:
    def __init__(self, locators: list[_FakeLocator]):
        self._locators = locators

    def count(self) -> int:  # noqa: D401 - mimic playwright API
        return len(self._locators)

    @property
    def first(self) -> _FakeLocator:
        return self._locators[0] if self._locators else _FakeLocator()

    def nth(self, index: int) -> _FakeLocator:
        return self._locators[index]


class _FakeFrame:
    def __init__(self, *, body: str = "", url: str = "", input_locator: _FakeLocator | None = None,
                 submit_locators: dict[str, _FakeLocator] | None = None,
                 locator_map: dict[str, _FakeLocator | _FakeLocatorGroup] | None = None):
        self._body = body
        self.url = url
        self._input = input_locator or _FakeLocator()
        self._submit = submit_locators or {}
        self._locator_map = locator_map or {}

    def locator(self, selector: str) -> _FakeLocator | _FakeLocatorGroup:
        if selector == 'body':
            loc = _FakeLocator()
            loc.inner_text = lambda timeout=0: self._body  # type: ignore
            return loc
        if selector in self._locator_map:
            return self._locator_map[selector]
        # 任意 input selector 都返回同一个候选输入框
        return self._input

    def get_by_text(self, text: str, exact: bool = False) -> _FakeLocator:
        return self._submit.get(text, _FakeLocator())


class _FakePage:
    def __init__(self, frames: list[_FakeFrame]):
        self.frames = frames

    @property
    def main_frame(self) -> _FakeFrame:
        return self.frames[0]

    def title(self) -> str:
        return "聚水潭ERP"

    def wait_for_timeout(self, ms: int) -> None:
        return None

    def screenshot(self, path: str = "") -> None:
        return None


class _FakeContext:
    def __init__(self, pages: list[_FakePage]):
        self.pages = pages


def _patch_connect(monkeypatch, context: _FakeContext) -> None:
    class _PW:
        def stop(self) -> None:
            return None

    monkeypatch.setattr(sv, "_connect", lambda: (_PW(), object(), context))


def test_mask_code_no_plaintext() -> None:
    assert sv.mask_code("1234") == "****"
    assert "1234" not in sv.mask_code("1234")


def test_detect_finds_dialog(monkeypatch) -> None:
    input_loc = _FakeLocator(visible=True, count=1)
    frame = _FakeFrame(body="请输入短信验证码", url="https://www.erp321.com/x", input_locator=input_loc)
    _patch_connect(monkeypatch, _FakeContext([_FakePage([frame])]))

    resp = sv.detect_sms_dialog()
    assert resp.success is True
    assert resp.command == "auth sms detect"
    assert resp.data["sms_required"] is True
    assert resp.data["source"] == "9222_chrome"
    assert "短信验证码" in resp.data["matched_signals"]


def test_detect_no_dialog(monkeypatch) -> None:
    frame = _FakeFrame(body="订单列表", url="https://www.erp321.com/orders")
    _patch_connect(monkeypatch, _FakeContext([_FakePage([frame])]))

    resp = sv.detect_sms_dialog()
    assert resp.success is True
    assert resp.data["sms_required"] is False


def test_detect_browser_not_running(monkeypatch) -> None:
    def _boom():
        raise sv._BrowserNotRunning("9222 closed")

    monkeypatch.setattr(sv, "_connect", _boom)
    resp = sv.detect_sms_dialog()
    assert resp.success is False
    assert resp.data["error_code"] == "BROWSER_NOT_RUNNING"


def test_submit_invalid_code(monkeypatch) -> None:
    resp = sv.submit_sms_code(code="12", execute=True)
    assert resp.success is False
    assert resp.data["error_code"] == "INVALID_CODE"
    assert resp.data["masked_code"] == "**"


def test_submit_requires_execute(monkeypatch) -> None:
    # 不传 execute：绝不连接浏览器、绝不提交。
    called = {"connect": False}

    def _connect_spy():
        called["connect"] = True
        raise AssertionError("execute=False 不应连接浏览器")

    monkeypatch.setattr(sv, "_connect", _connect_spy)
    resp = sv.submit_sms_code(code="1234", execute=False)
    assert resp.success is False
    assert resp.data["error_code"] == "EXECUTE_REQUIRED"
    assert resp.data["masked_code"] == "****"
    assert called["connect"] is False


def test_submit_success_and_no_plaintext(monkeypatch) -> None:
    input_loc = _FakeLocator(visible=True, count=1)
    submit_btn = _FakeLocator(visible=True, count=1)
    dialog_frame = _FakeFrame(
        body="请输入短信验证码",
        url="https://www.erp321.com/x",
        input_locator=input_loc,
        submit_locators={"确定": submit_btn},
    )
    clean_frame = _FakeFrame(body="订单列表", url="https://www.erp321.com/orders")

    # 第一次检测命中弹窗，提交后复检返回干净页面。
    contexts = iter([
        _FakeContext([_FakePage([dialog_frame])]),
        _FakeContext([_FakePage([dialog_frame])]),
    ])

    # _pick_page_and_frame 被调用两次：提交前(有弹窗)、提交后(无弹窗)。
    states = iter([True, False])

    real_scan = sv._scan_frame

    def fake_scan(frame):
        result = real_scan(frame)
        # 提交后把 dialog_frame 视为已消失
        try:
            result = dict(result)
            result["sms_required"] = next(states) if frame is dialog_frame else result["sms_required"]
        except StopIteration:
            result["sms_required"] = False
        return result

    monkeypatch.setattr(sv, "_scan_frame", fake_scan)

    class _PW:
        def stop(self) -> None:
            return None

    monkeypatch.setattr(sv, "_connect", lambda: (_PW(), object(), _FakeContext([_FakePage([dialog_frame])])))

    resp = sv.submit_sms_code(code="1234", execute=True)
    assert resp.success is True, resp.data
    assert resp.data["submitted"] is True
    assert resp.data["verified"] is True
    assert resp.data["masked_code"] == "****"
    # 关键：返回 JSON 任何角落都不得出现明文验证码
    assert "1234" not in json.dumps(resp.data, ensure_ascii=False)
    assert input_loc.filled == ["", "1234"] or input_loc.filled == ["1234"]


def test_submit_real_sms_verification_iframe_single_digit_inputs(monkeypatch) -> None:
    inputs = [_FakeLocator(visible=True, count=1) for _ in range(4)]
    input_group = _FakeLocatorGroup(inputs)
    submit_btn = _FakeLocator(visible=True, count=1)
    dialog_frame = _FakeFrame(
        body="授权验证 为了您的数据安全，查询轨迹要求验证身份，已发送验证码到您手机：156*****388，请输入验证码后进行确认。40秒后重新发送 取 消 确 认",
        url="https://src.erp321.com/erp-web-group/erp-components-site/sms-verification?jmxToken=x&action=%E6%9F%A5%E8%AF%A2%E8%BD%A8%E8%BF%B9",
        submit_locators={"确 认": submit_btn},
        locator_map={'input.ant-input[maxlength="1"]': input_group},
    )

    states = iter([True, False])
    real_scan = sv._scan_frame

    def fake_scan(frame):
        result = dict(real_scan(frame))
        if frame is dialog_frame:
            try:
                result["sms_required"] = next(states)
            except StopIteration:
                result["sms_required"] = False
        return result

    monkeypatch.setattr(sv, "_scan_frame", fake_scan)

    class _PW:
        def stop(self) -> None:
            return None

    monkeypatch.setattr(sv, "_connect", lambda: (_PW(), object(), _FakeContext([_FakePage([dialog_frame])])))

    resp = sv.submit_sms_code(code="1234", execute=True)
    assert resp.success is True, resp.data
    assert [item.filled[-1] for item in inputs] == ["1", "2", "3", "4"]
    assert submit_btn.clicked == 1
    assert "1234" not in json.dumps(resp.data, ensure_ascii=False)


def test_submit_input_not_found(monkeypatch) -> None:
    # 命中弹窗文本但没有可见输入框
    frame = _FakeFrame(body="请输入短信验证码", url="https://www.erp321.com/x",
                       input_locator=_FakeLocator(visible=False, count=0))
    _patch_connect(monkeypatch, _FakeContext([_FakePage([frame])]))
    resp = sv.submit_sms_code(code="1234", execute=True)
    assert resp.success is False
    assert resp.data["error_code"] in {"SMS_INPUT_NOT_FOUND", "SMS_DIALOG_NOT_FOUND"}


def test_detect_extracts_phone_mask(monkeypatch) -> None:
    input_loc = _FakeLocator(visible=True, count=1)
    frame = _FakeFrame(
        body="授权验证 已发送验证码到您手机：156*****388，请输入验证码后进行确认。",
        url="https://src.erp321.com/erp-components-site/sms-verification",
        input_locator=input_loc,
    )
    _patch_connect(monkeypatch, _FakeContext([_FakePage([frame])]))
    resp = sv.detect_sms_dialog()
    assert resp.success is True
    assert resp.data["sms_required"] is True
    assert resp.data["phone_mask"] == "156*****388"


def test_submit_accepts_six_digit_code(monkeypatch) -> None:
    inputs = [_FakeLocator(visible=True, count=1) for _ in range(6)]
    group = _FakeLocatorGroup(inputs)
    submit_btn = _FakeLocator(visible=True, count=1)
    dialog_frame = _FakeFrame(
        body="请输入短信验证码",
        url="https://www.erp321.com/x",
        submit_locators={"确定": submit_btn},
        locator_map={'input[maxlength="1"]': group},
    )
    states = iter([True, False])
    real_scan = sv._scan_frame

    def fake_scan(frame):
        result = dict(real_scan(frame))
        if frame is dialog_frame:
            try:
                result["sms_required"] = next(states)
            except StopIteration:
                result["sms_required"] = False
        return result

    monkeypatch.setattr(sv, "_scan_frame", fake_scan)

    class _PW:
        def stop(self) -> None:
            return None

    monkeypatch.setattr(sv, "_connect", lambda: (_PW(), object(), _FakeContext([_FakePage([dialog_frame])])))

    resp = sv.submit_sms_code(code="123456", execute=True)
    assert resp.success is True, resp.data
    assert [item.filled[-1] for item in inputs] == ["1", "2", "3", "4", "5", "6"]
    assert resp.data["masked_code"] == "******"
    assert "123456" not in json.dumps(resp.data, ensure_ascii=False)


def test_submit_rejects_too_long_code() -> None:
    resp = sv.submit_sms_code(code="1234567", execute=True)
    assert resp.success is False
    assert resp.data["error_code"] == "INVALID_CODE"


def test_classify_sms_text_maps_to_auth_sms_required() -> None:
    from ops_cli.execution import _classify_error

    code, retryable, hint = _classify_error(RuntimeError("查询轨迹需要完成短信验证。请先在聚水潭物流查询页面完成授权后重新执行。"))
    assert code == "AUTH_SMS_REQUIRED"
    assert retryable is False
    assert hint
