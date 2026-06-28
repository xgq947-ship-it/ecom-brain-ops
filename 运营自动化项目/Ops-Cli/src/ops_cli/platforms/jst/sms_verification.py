"""聚水潭短信验证码弹窗：9222 Chrome 检测 + 填写提交。

边界：本模块是平台能力层，负责连接项目现有 9222 专用 Chrome、跨 page/frame 检测
聚水潭短信验证码弹窗、把用户主动提供的验证码填入并提交。所有 Selector / CDP /
Playwright / 平台信号都收敛在这里，业务层（workflow）只消费 JSON。

安全约束（与上层 workflow 一致）：
- 只填写调用方显式传入的验证码；绝不读取短信、绝不破解/绕过。
- detect 为只读：不导航、不刷新、不点击，避免误关掉已弹出的验证码窗口。
- submit 仅在 execute=True 时才真正填写并提交。
- 任何返回值、日志、截图都不输出验证码明文，只输出 masked_code。
- 不关闭浏览器、不清 cookie、不重置 session。
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from ops_cli.config import get_config
from ops_cli.output import CommandResponse


SOURCE = "9222_chrome"
SCENE = "jst/sms_verification"
DEFAULT_SCREENSHOT_DIR = Path("runtime/screenshots")

# 聚水潭域名信号（用于在多个标签页里挑出聚水潭页面）。
_JST_DOMAIN_SIGNALS = ("erp321.com", "jushuitan", "epaas")
_SMS_FRAME_URL_SIGNALS = ("sms-verification", "erp-components-site/sms-verification")

# 验证码弹窗文本信号。
_TEXT_SIGNALS = (
    "授权验证",
    "为了您的数据安全",
    "查询轨迹要求验证身份",
    "已发送验证码到您手机",
    "请输入验证码后进行确认",
    "短信验证码",
    "请输入验证码",
    "验证码",
)

# 候选验证码输入框 selector（多候选，平台 DOM 不稳定时逐个尝试）。
_INPUT_SELECTORS = (
    'input.ant-input[maxlength="1"]',
    'input[maxlength="1"]',
    'input[placeholder*="验证码"]',
    'input[name*="code"]',
    'input[name*="captcha"]',
    'input[id*="code"]',
    'input[id*="captcha"]',
    'input[maxlength="4"]',
    'input[maxlength="6"]',
)

# 候选提交按钮文本。
_SUBMIT_TEXTS = ("确 认", "确认", "确定", "提交", "验证", "登录", "立即验证")
_STRONG_TEXT_SIGNALS = (
    "授权验证",
    "查询轨迹要求验证身份",
    "已发送验证码到您手机",
    "短信验证码",
    "请输入验证码",
    "请输入验证码后进行确认",
)


def mask_code(code: str) -> str:
    """把验证码整体打码，绝不泄露明文。"""
    length = len(code) if code else 4
    return "*" * max(length, 1)


# 验证码长度：真实查询轨迹授权弹窗是 4 位；放宽到 4-6 位以兼容平台可能的 6 位短信码。
_MIN_CODE_LEN = 4
_MAX_CODE_LEN = 6

# 弹窗里的脱敏手机号，例如 156*****388 / 156****388。
_PHONE_MASK_RE = re.compile(r"(\d{3}\*{2,}\d{2,4})")


def _extract_phone_mask(body: str) -> str | None:
    match = _PHONE_MASK_RE.search(body or "")
    return match.group(1) if match else None


def _safe_body_text(frame: Any) -> str:
    try:
        return frame.locator("body").inner_text(timeout=2000)
    except Exception:
        return ""


def _frame_has_visible_input(frame: Any) -> bool:
    for selector in _INPUT_SELECTORS:
        try:
            locator = frame.locator(selector)
            if locator.count() and locator.first.is_visible(timeout=800):
                return True
        except Exception:
            continue
    return False


def _scan_frame(frame: Any) -> dict[str, Any]:
    """检测单个 frame 是否含验证码信号，返回信号明细（不含任何用户输入）。"""
    body = _safe_body_text(frame)
    matched_texts = [signal for signal in _TEXT_SIGNALS if signal in body]
    has_input = _frame_has_visible_input(frame)
    url = (getattr(frame, "url", "") or "")
    lower_url = url.lower()
    is_jst = any(token in lower_url for token in _JST_DOMAIN_SIGNALS) or any(
        token in body for token in ("聚水潭", "ERP")
    )
    is_sms_frame = any(token in lower_url for token in _SMS_FRAME_URL_SIGNALS)
    # 仅出现"验证码"文本不足以判定（可能是无关文案）；要求同时存在可见输入框，
    # 或出现强信号"短信验证码"/"请输入验证码"。
    strong_text = any(sig in body for sig in _STRONG_TEXT_SIGNALS)
    sms_required = bool(has_input and matched_texts) or bool(is_sms_frame and has_input) or strong_text
    signals: list[str] = []
    if matched_texts:
        signals.extend(matched_texts)
    if has_input:
        signals.append("验证码输入框")
    if is_sms_frame:
        signals.append("sms-verification iframe")
    return {
        "sms_required": sms_required,
        "matched_signals": signals,
        "has_input": has_input,
        "is_jst": is_jst,
        "phone_mask": _extract_phone_mask(body),
    }


def _pick_page_and_frame(context: Any) -> tuple[Any | None, Any | None, dict[str, Any]]:
    """在所有 page/frame 中挑出最可能含验证码弹窗的目标。

    优先级：含验证码信号的 frame > 聚水潭域名页 > 当前活动页。返回 (page, frame, scan)。
    """
    best: tuple[Any, Any, dict[str, Any]] | None = None
    jst_fallback: tuple[Any, Any, dict[str, Any]] | None = None
    for page in context.pages:
        for frame in page.frames:
            try:
                scan = _scan_frame(frame)
            except Exception:
                continue
            if scan["sms_required"]:
                return page, frame, scan
            if scan["is_jst"] and jst_fallback is None:
                jst_fallback = (page, frame, scan)
    if best is not None:
        return best
    if jst_fallback is not None:
        return jst_fallback
    # 没有任何聚水潭页：退回第一个 page 的主 frame。
    if context.pages:
        page = context.pages[0]
        return page, page.main_frame, {"sms_required": False, "matched_signals": [], "has_input": False, "is_jst": False, "phone_mask": None}
    return None, None, {"sms_required": False, "matched_signals": [], "has_input": False, "is_jst": False, "phone_mask": None}


def _find_input_locator(frame: Any) -> Any | None:
    for selector in _INPUT_SELECTORS:
        try:
            candidate = frame.locator(selector)
            if candidate.count() and candidate.first.is_visible(timeout=1000):
                return candidate
        except Exception:
            continue
    return None


def _fill_sms_code(input_locator: Any, code: str) -> None:
    if input_locator.count() >= len(code):
        for index, digit in enumerate(code):
            item = input_locator.nth(index)
            try:
                item.fill("", timeout=2000)
            except Exception:
                pass
            item.fill(digit, timeout=3000)
        return

    field = input_locator.first
    try:
        field.fill("", timeout=2000)
    except Exception:
        pass
    field.fill(code, timeout=3000)


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _connect():
    """连接项目现有 9222 专用 Chrome。返回 (playwright_ctx_manager, browser, context)。

    失败时抛 _BrowserNotRunning，由上层翻译成 BROWSER_NOT_RUNNING。
    """
    root = _sessionhub_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scene.chrome_cdp import CDP_URL  # type: ignore
    from playwright.sync_api import Error as PlaywrightError  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(CDP_URL)
    except PlaywrightError as exc:
        pw.stop()
        raise _BrowserNotRunning(str(exc)) from exc
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    return pw, browser, context


class _BrowserNotRunning(RuntimeError):
    pass


def _failure(command: str, error_code: str, message: str, *, retryable: bool = False, **extra: Any) -> CommandResponse:
    data = {
        "error_code": error_code,
        "error": message,
        "retryable": retryable,
        "source": SOURCE,
        "scene": SCENE,
        "artifacts": [],
    }
    data.update(extra)
    return CommandResponse(success=False, platform="jst", command=command, data=data)


def _screenshot_dir(screenshot_dir: str | None) -> Path:
    base = Path(screenshot_dir).expanduser() if screenshot_dir else (Path.cwd() / DEFAULT_SCREENSHOT_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _capture_screenshot(page: Any, screenshot_dir: str | None, prefix: str) -> str | None:
    """检测阶段截图（此时尚未填入任何验证码，图中不含明文）。"""
    try:
        base = _screenshot_dir(screenshot_dir)
        # 文件名只含时间戳，绝不含验证码。
        path = base / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return None


def detect_sms_dialog(*, screenshot_dir: str | None = None, dry_run: bool = False) -> CommandResponse:
    """检测当前 9222 Chrome 是否存在聚水潭短信验证码弹窗（只读）。"""
    try:
        pw, _browser, context = _connect()
    except _BrowserNotRunning as exc:
        return _failure(
            "auth sms detect",
            "BROWSER_NOT_RUNNING",
            f"未连接到 9222 专用 Chrome：{exc}。请先在 9222 专用 Chrome 登录聚水潭。",
            retryable=True,
        )
    try:
        page, _frame, scan = _pick_page_and_frame(context)
        screenshot_path = None
        if page is not None:
            screenshot_path = _capture_screenshot(page, screenshot_dir, "jst_sms_detect")
        return CommandResponse(
            success=True,
            platform="jst",
            command="auth sms detect",
            data={
                "sms_required": bool(scan["sms_required"]),
                "matched_signals": list(scan["matched_signals"]),
                "phone_mask": scan.get("phone_mask"),
                "page_title": page.title() if page is not None else "",
                "screenshot_path": screenshot_path,
                "source": SOURCE,
                "scene": SCENE,
                "dry_run": dry_run,
                "artifacts": [],
            },
        )
    finally:
        pw.stop()


def submit_sms_code(*, code: str, execute: bool = False, screenshot_dir: str | None = None) -> CommandResponse:
    """把用户提供的验证码填入聚水潭弹窗并提交。

    仅 execute=True 时真正填写/提交；否则返回 EXECUTE_REQUIRED，绝不写入。
    """
    masked = mask_code(code)
    if not (code and code.isdigit() and _MIN_CODE_LEN <= len(code) <= _MAX_CODE_LEN):
        return _failure(
            "auth sms submit",
            "INVALID_CODE",
            f"验证码必须是 {_MIN_CODE_LEN}-{_MAX_CODE_LEN} 位数字。",
            masked_code=masked,
        )
    if not execute:
        return _failure(
            "auth sms submit",
            "EXECUTE_REQUIRED",
            "未传入 --execute，按安全规则不填写、不提交验证码。",
            masked_code=masked,
        )

    try:
        pw, _browser, context = _connect()
    except _BrowserNotRunning as exc:
        return _failure(
            "auth sms submit",
            "BROWSER_NOT_RUNNING",
            f"未连接到 9222 专用 Chrome：{exc}。",
            retryable=True,
            masked_code=masked,
        )
    try:
        page, frame, scan = _pick_page_and_frame(context)
        if page is None or frame is None:
            return _failure("auth sms submit", "SMS_DIALOG_NOT_FOUND", "未找到聚水潭页面或验证码弹窗。", masked_code=masked)
        if not scan["sms_required"]:
            return _failure("auth sms submit", "SMS_DIALOG_NOT_FOUND", "当前页面未检测到短信验证码弹窗。", masked_code=masked)

        # 定位验证码输入框。真实查询轨迹授权弹窗是 4 个 maxlength=1 输入框。
        input_locator = _find_input_locator(frame)
        if input_locator is None:
            return _failure("auth sms submit", "SMS_INPUT_NOT_FOUND", "未定位到验证码输入框。", masked_code=masked)

        _fill_sms_code(input_locator, code)
        page.wait_for_timeout(300)

        # 定位并点击提交按钮。
        clicked = False
        for text in _SUBMIT_TEXTS:
            try:
                button = frame.get_by_text(text, exact=True).first
                if button.count() and button.is_visible(timeout=800):
                    button.click(timeout=2000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            return _failure("auth sms submit", "SMS_SUBMIT_BUTTON_NOT_FOUND", "未定位到验证码提交按钮。", masked_code=masked)

        page.wait_for_timeout(2500)

        # 复检：弹窗是否消失。
        _page2, _frame2, scan_after = _pick_page_and_frame(context)
        verified = not bool(scan_after["sms_required"])
        if not verified:
            return _failure(
                "auth sms submit",
                "SMS_VERIFY_FAILED",
                "已提交验证码，但弹窗仍存在，验证可能未通过。",
                retryable=True,
                masked_code=masked,
                submitted=True,
                verified=False,
            )

        return CommandResponse(
            success=True,
            platform="jst",
            command="auth sms submit",
            data={
                "submitted": True,
                "verified": True,
                "masked_code": masked,
                "source": SOURCE,
                "scene": SCENE,
                "artifacts": [],
            },
        )
    finally:
        pw.stop()
