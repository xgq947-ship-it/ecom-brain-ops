"""TMCS fund page readers.

This module only reads rendered page text and saves screenshots through the
9222 SessionHub browser. It does not call TMCS APIs or replay captured requests.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.platforms.tmcs.shared import TMCS_SITE
from ops_cli.runtime_context import write_runtime_context


RECEIVABLE_FIELD_NAME = "商家开票含税总额"
RECEIVABLE_SCENE = "fund_receivable_bill_sum"
PROMOTION_SCENE = "fund_promotion_balance_sum"

def _tmcs_frame_url(inner_path: str) -> str:
    inner = f"https://web.txcs.tmall.com{inner_path}"
    return f"https://web.txcs.tmall.com/?frameUrl={quote(inner, safe='')}"


TMCS_STATEMENT_BILL_URL = _tmcs_frame_url("/pages/chaoshi/settlement_confirm_query_list")
TMCS_PROMOTION_PLATFORM_URL = _tmcs_frame_url("/pages/chaoshi/vendor_jbp_page_new")

SIMULATED_RECEIVABLE_AMOUNTS = [123.45, 678.90]
SIMULATED_PROMOTION_BALANCES = {"jubao_pen": 100.0, "zhiduoxing": 200.0, "wanxiangtai": 300.0}

_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _money(value: str) -> float:
    cleaned = value.replace(",", "").replace("¥", "").replace("￥", "").replace("元", "").strip()
    return round(float(cleaned), 2)


def _money_values(text: str) -> list[float]:
    pattern = re.compile(r"[-+]?[$¥￥]?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?|[-+]?[$¥￥]?\s*\d+(?:\.\d+)?")
    values: list[float] = []
    for match in pattern.finditer(text or ""):
        token = match.group(0)
        if not re.search(r"\d", token):
            continue
        values.append(_money(token))
    return values


def receivable_bill_month_range(month: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", month)
    if not match:
        raise RuntimeError("INVALID_MONTH：月份格式必须为 YYYY-MM。")
    year = int(match.group(1))
    month_num = int(match.group(2))
    if month_num < 1 or month_num > 12:
        raise RuntimeError("INVALID_MONTH：月份格式必须为 YYYY-MM。")
    next_year = year + 1 if month_num == 12 else year
    next_month = 1 if month_num == 12 else month_num + 1
    return f"{year:04d}-{month_num:02d}-01", f"{next_year:04d}-{next_month:02d}-01"


def extract_receivable_amounts_from_table_rows(headers: list[str], rows: list[list[str]], *, month: str | None = None) -> list[float]:
    normalized_headers = [re.sub(r"\s+", "", header or "") for header in headers]
    try:
        column_index = normalized_headers.index(RECEIVABLE_FIELD_NAME)
    except ValueError as exc:
        raise RuntimeError(f"FIELD_NOT_FOUND：页面未找到字段「{RECEIVABLE_FIELD_NAME}」。") from exc
    period_index = normalized_headers.index("账单周期") if month and "账单周期" in normalized_headers else None

    amounts: list[float] = []
    for row in rows:
        if column_index >= len(row):
            continue
        if period_index is not None:
            if period_index >= len(row):
                continue
            period = re.sub(r"\s+", "", row[period_index] or "")
            if not period.startswith(month):
                continue
        cell = (row[column_index] or "").strip()
        values = _money_values(cell)
        if values:
            amounts.append(values[0])
    if not amounts:
        raise RuntimeError("AMOUNT_PARSE_FAILED：未解析到商家开票含税总额金额。")
    return amounts


def extract_receivable_amounts_from_text(text: str) -> list[float]:
    if RECEIVABLE_FIELD_NAME not in (text or ""):
        raise RuntimeError(f"FIELD_NOT_FOUND：页面未找到字段「{RECEIVABLE_FIELD_NAME}」。")

    lines = [line.strip() for line in (text or "").splitlines()]
    start = next((idx for idx, line in enumerate(lines) if RECEIVABLE_FIELD_NAME in line), -1)
    if start < 0:
        raise RuntimeError(f"FIELD_NOT_FOUND：页面未找到字段「{RECEIVABLE_FIELD_NAME}」。")

    candidates: list[str] = []
    same_line = lines[start].split(RECEIVABLE_FIELD_NAME, 1)[1]
    if same_line.strip():
        candidates.append(same_line)
    for line in lines[start + 1 :]:
        if not line:
            continue
        if re.search(r"[\u4e00-\u9fff]", line) and not re.search(r"^[¥￥元,\d.\-+\s]+$", line):
            break
        candidates.append(line)

    if not candidates:
        tail = text.split(RECEIVABLE_FIELD_NAME, 1)[1]
        candidates = [tail]

    amounts = _money_values("\n".join(candidates))
    if not amounts:
        raise RuntimeError("AMOUNT_PARSE_FAILED：未解析到商家开票含税总额金额。")
    return amounts


def sum_receivable_amounts(amounts: list[float]) -> float:
    return round(sum(float(item) for item in amounts), 2)


def extract_promotion_balances_from_text(text: str) -> dict[str, float]:
    labels = {
        "jubao_pen": "聚宝盆余额",
        "zhiduoxing": "智多星余额",
        "wanxiangtai": "万相台余额",
    }
    balances: dict[str, float] = {}
    for key, label in labels.items():
        match = re.search(re.escape(label) + r"[^0-9\-+¥￥]{0,30}([-+]?[$¥￥]?\s*\d[\d,]*(?:\.\d+)?)", text or "")
        if not match:
            raise RuntimeError(f"FIELD_NOT_FOUND：页面未找到字段「{label}」。")
        balances[key] = _money(match.group(1))
    return balances


def sum_promotion_balances(balances: dict[str, float]) -> float:
    return round(sum(float(balances[key]) for key in ("jubao_pen", "zhiduoxing", "wanxiangtai")), 2)


def _is_login_page(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    text = url.lower()
    path = (parsed.path or "").lower()
    return "login" in text or path.startswith("/member/login")


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _ensure_screenshot_dir(path: str | Path) -> Path:
    directory = Path(path).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_placeholder_screenshot(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_PNG_BYTES)
    return path


_FREEZE_ANIMATIONS_JS = r"""
() => {
  try {
    // 杀掉 requestAnimationFrame 循环（ECharts 等图表的持续重绘会让截图等不到稳定帧）
    window.requestAnimationFrame = function () { return 0; };
    window.cancelAnimationFrame = function () {};
    const style = document.createElement('style');
    style.setAttribute('data-ops-freeze', '1');
    style.textContent = `*, *::before, *::after {
      animation: none !important;
      animation-duration: 0s !important;
      animation-play-state: paused !important;
      transition: none !important;
      caret-color: transparent !important;
    }`;
    document.head.appendChild(style);
    // 隐藏持续重绘的 GIF / canvas / video，避免截图等待稳定帧超时
    document.querySelectorAll('img[src*=".gif"], canvas, video').forEach((el) => {
      el.style.visibility = 'hidden';
    });
  } catch (e) {}
}
"""


def _freeze_page_for_screenshot(page: Any) -> None:
    """注入 JS 杀掉 rAF 重绘循环、冻结动画并隐藏 GIF/canvas，避免截图无限等待稳定帧。"""
    for frame in page.frames:
        try:
            frame.evaluate(_FREEZE_ANIMATIONS_JS)
        except Exception:
            continue


def _bounded_call(fn: Any, *, seconds: int) -> Any:
    """用 SIGALRM 给同步调用加硬超时，避免底层抓帧无限挂起拖死整个进程。

    仅在主线程可用；非主线程降级为直接调用。
    """
    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        return fn()

    def _handler(signum, frame):  # noqa: ANN001
        raise TimeoutError(f"操作超过 {seconds}s 硬超时。")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        return fn()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _save_viewport_screenshot(
    page: Any,
    path: Path,
    *,
    timeout_ms: int = 12000,
    animations: str = "allow",
    retries: int = 1,
    freeze: bool = True,
) -> Path:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    path.parent.mkdir(parents=True, exist_ok=True)
    # 9222 Chrome 窗口在后台被遮挡时合成器不产帧，截图会无限等待稳定帧。
    # 激活标签页（CDP Page.bringToFront，不抢 OS 焦点）即可让渲染器恢复产帧。
    try:
        page.bring_to_front()
        page.wait_for_timeout(300)
    except Exception:
        pass
    if freeze:
        _freeze_page_for_screenshot(page)
        page.wait_for_timeout(600)
    hard_cap = max(int(timeout_ms / 1000) + 5, 8)
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            _bounded_call(
                lambda: page.screenshot(
                    path=str(path),
                    full_page=False,
                    timeout=timeout_ms,
                    animations=animations,
                ),
                seconds=hard_cap,
            )
            return path
        except (PlaywrightError, TimeoutError) as exc:
            last_exc = exc
            if attempt < retries:
                page.wait_for_timeout(1000)
    raise RuntimeError(f"SCREENSHOT_FAILED：截图失败：{last_exc}")


def _collect_page_text(page: Any) -> str:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    parts: list[str] = []
    for frame in page.frames:
        try:
            parts.append(frame.locator("body").inner_text(timeout=2000))
        except PlaywrightError:
            continue
    return "\n".join(parts)


def _wait_for_page_text(page: Any, required_text: str, *, timeout_ms: int = 30000) -> str:
    text = ""
    waited_ms = 0
    while waited_ms <= timeout_ms:
        text = _collect_page_text(page)
        if required_text in text:
            return text
        page.wait_for_timeout(1500)
        waited_ms += 1500
    raise RuntimeError(f"FIELD_NOT_FOUND：页面未找到字段「{required_text}」。")


def _wait_for_page_ready(page: Any, ready_check: Any, *, label: str, timeout_ms: int = 45000) -> str:
    """轮询页面文本，直到 ready_check(text) 为真（用于异步加载的数值）。"""
    text = ""
    waited_ms = 0
    while waited_ms <= timeout_ms:
        text = _collect_page_text(page)
        try:
            if ready_check(text):
                return text
        except Exception:
            pass
        page.wait_for_timeout(1500)
        waited_ms += 1500
    raise RuntimeError(f"FIELD_NOT_FOUND：页面未找到字段「{label}」。")


def _read_page_text_and_screenshot(
    *,
    target_url: str,
    screenshot_path: Path,
    required_text: str | None = None,
    ready_check: Any = None,
    ready_label: str | None = None,
) -> str:
    root = _sessionhub_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from scene.chrome_cdp import CDP_URL, bring_chrome_to_front, start_chrome  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"无法加载 SessionHub Chrome 依赖：{exc}") from exc

    ok, msg = start_chrome()
    if not ok:
        raise RuntimeError(msg)

    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("缺少 Playwright，请先运行：pip install -r requirements.txt") from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        created_page = len(context.pages) == 1 and page.url == "about:blank"
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            if _is_login_page(page.url):
                bring_chrome_to_front()
                raise RuntimeError("TMCS_LOGIN_REQUIRED：检测到猫超登录页，已切到前台，请先完成登录后重试。")

            if ready_check is not None:
                text = _wait_for_page_ready(page, ready_check, label=ready_label or required_text or "页面数据")
            elif required_text:
                text = _wait_for_page_text(page, required_text)
            else:
                text = _collect_page_text(page)

            _save_viewport_screenshot(page, screenshot_path)
            return text
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass


def _apply_receivable_month_filter(page: Any, month: str) -> None:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    start_date, end_month = receivable_bill_month_range(month)
    target_frame = None
    for frame in page.frames:
        try:
            if "账单生成时间" in frame.locator("body").inner_text(timeout=1000):
                target_frame = frame
                break
        except PlaywrightError:
            continue
    if target_frame is None:
        return

    filled = False
    try:
        filled = bool(
            target_frame.evaluate(
                """({startDate, endMonth}) => {
                    const visible = element => {
                        const style = window.getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                    };
                    const labels = Array.from(document.querySelectorAll('*')).filter(element => {
                        const text = (element.textContent || '').replace(/\\s+/g, '');
                        return visible(element) && text.includes('账单生成时间') && text.length <= 12;
                    });
                    const label = labels.sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (ar.width * ar.height) - (br.width * br.height);
                    })[0];
                    if (!label) return false;
                    const labelRect = label.getBoundingClientRect();
                    let inputs = Array.from(document.querySelectorAll('input')).filter(input => {
                        if (!visible(input) || input.type === 'hidden') return false;
                        const rect = input.getBoundingClientRect();
                        return Math.abs(rect.top - labelRect.top) < 120 && rect.left >= labelRect.left - 20;
                    });
                    const dated = inputs.filter(input => /^\\d{4}-\\d{2}/.test(input.value || ''));
                    if (dated.length >= 2) inputs = dated;
                    inputs = inputs
                        .map(input => ({input, rect: input.getBoundingClientRect()}))
                        .sort((a, b) => (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left))
                        .map(item => item.input)
                        .slice(0, 2);
                    if (inputs.length < 2) return false;
                    const setValue = (input, value) => {
                        const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
                        descriptor.set.call(input, value);
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        input.dispatchEvent(new Event('blur', { bubbles: true }));
                        input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter' }));
                        input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter' }));
                    };
                    setValue(inputs[0], startDate);
                    setValue(inputs[1], endMonth);
                    return true;
                }""",
                {"startDate": start_date, "endMonth": end_month},
            )
        )
    except PlaywrightError:
        filled = False

    if not filled:
        return

    try:
        target_frame.get_by_text("查询", exact=True).last.click(timeout=3000)
    except PlaywrightError:
        try:
            target_frame.locator("button").filter(has_text="查询").last.click(timeout=3000)
        except PlaywrightError:
            return
    page.wait_for_timeout(2500)


def _scroll_receivable_field_into_view(page: Any) -> None:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    script = """headerText => {
        const normalize = text => (text || '').replace(/\\s+/g, '').trim();
        const visibleEnough = element => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
        };
        const target = Array.from(document.querySelectorAll('*')).find(element => {
            const text = normalize(element.textContent);
            return visibleEnough(element) && (text === headerText || (text.includes(headerText) && text.length <= headerText.length + 4));
        });
        if (!target) return false;
        target.scrollIntoView({block: 'center', inline: 'center'});
        return true;
    }"""
    for frame in page.frames:
        try:
            if RECEIVABLE_FIELD_NAME not in frame.locator("body").inner_text(timeout=1000):
                continue
            if frame.evaluate(script, RECEIVABLE_FIELD_NAME):
                page.wait_for_timeout(500)
                return
        except PlaywrightError:
            continue


def _extract_receivable_amounts_from_page(page: Any, *, month: str | None = None) -> list[float]:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    script = """headerText => {
        const normalize = text => (text || '').replace(/\\s+/g, '').trim();
        const visible = element => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
        };
        const headerElements = Array.from(document.querySelectorAll('*'))
            .filter(element => {
                const text = normalize(element.textContent);
                return visible(element) && (text === headerText || (text.includes(headerText) && text.length <= headerText.length + 4));
            });
        const header = headerElements[0];
        const table = header && (header.closest('.next-table') || header.closest('table') || header.closest('[role="table"]'));
        if (!table) return {headers: [], rows: []};
        let headers = Array.from(table.querySelectorAll('.next-table-header .next-table-cell, th, [role="columnheader"]'))
            .map(element => normalize(element.textContent));
        const firstDuplicate = headers.findIndex((headerText, index) => index > 0 && headerText === headers[0]);
        if (firstDuplicate > 0) headers = headers.slice(0, firstDuplicate);
        const rowNodes = Array.from(table.querySelectorAll('.next-table-body .next-table-row, tbody tr, [role="row"]'))
            .filter(row => visible(row));
        const rows = rowNodes.map(row =>
            Array.from(row.querySelectorAll('.next-table-cell, td, [role="cell"]'))
                .map(cell => normalize(cell.textContent))
        ).filter(row => row.length > 0);
        return {headers, rows};
    }"""
    last_text = ""
    for frame in page.frames:
        try:
            last_text = frame.locator("body").inner_text(timeout=1000)
            if RECEIVABLE_FIELD_NAME not in last_text:
                continue
            result = frame.evaluate(script, RECEIVABLE_FIELD_NAME)
            if not isinstance(result, dict):
                continue
            headers = result.get("headers") or []
            rows = result.get("rows") or []
            if headers and rows:
                return extract_receivable_amounts_from_table_rows(headers, rows, month=month)
        except PlaywrightError:
            continue
    return extract_receivable_amounts_from_text(last_text)


def _read_receivable_bill_page(*, month: str, screenshot_dir: str | Path) -> tuple[list[float], Path]:
    screenshot = _ensure_screenshot_dir(screenshot_dir) / f"receivable_bill_{month}_{_timestamp()}.png"
    root = _sessionhub_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from scene.chrome_cdp import CDP_URL, bring_chrome_to_front, start_chrome  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"无法加载 SessionHub Chrome 依赖：{exc}") from exc

    ok, msg = start_chrome()
    if not ok:
        raise RuntimeError(msg)

    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("缺少 Playwright，请先运行：pip install -r requirements.txt") from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        created_page = len(context.pages) == 1 and page.url == "about:blank"
        try:
            page.goto(TMCS_STATEMENT_BILL_URL, wait_until="domcontentloaded", timeout=30000)
            if _is_login_page(page.url):
                bring_chrome_to_front()
                raise RuntimeError("TMCS_LOGIN_REQUIRED：检测到猫超登录页，已切到前台，请先完成登录后重试。")
            _wait_for_page_text(page, RECEIVABLE_FIELD_NAME)
            _scroll_receivable_field_into_view(page)
            amounts = _extract_receivable_amounts_from_page(page, month=month)
            _save_viewport_screenshot(page, screenshot)
            return amounts, screenshot
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass


def _promotion_text_ready(text: str) -> bool:
    """三项余额（聚宝盆/智多星/万相台）都能解析出数字时才算加载完成。"""
    try:
        extract_promotion_balances_from_text(text)
        return True
    except RuntimeError:
        return False


def _read_promotion_balance_page(*, screenshot_dir: str | Path) -> tuple[str, Path]:
    screenshot = _ensure_screenshot_dir(screenshot_dir) / f"promotion_balance_{_timestamp()}.png"
    text = _read_page_text_and_screenshot(
        target_url=TMCS_PROMOTION_PLATFORM_URL,
        screenshot_path=screenshot,
        ready_check=_promotion_text_ready,
        ready_label="聚宝盆余额",
    )
    return text, screenshot


def run_receivable_bill_sum(*, month: str, screenshot_dir: str | Path, dry_run: bool = False) -> CommandResponse:
    inputs = {"month": month, "screenshot_dir": str(screenshot_dir), "dry_run": dry_run}
    scene = f"{TMCS_SITE}/{RECEIVABLE_SCENE}"
    if dry_run:
        amounts = list(SIMULATED_RECEIVABLE_AMOUNTS)
        screenshot = _write_placeholder_screenshot(_ensure_screenshot_dir(screenshot_dir) / f"receivable_bill_{month}_{_timestamp()}.png")
        source = "simulated"
        simulated = True
    else:
        amounts, screenshot = _read_receivable_bill_page(month=month, screenshot_dir=screenshot_dir)
        source = "page"
        simulated = False

    total = sum_receivable_amounts(amounts)
    context_path = write_runtime_context(
        task_name="tmcs_fund_receivable_bill_sum",
        status="success",
        inputs=inputs,
        outputs={"amounts": amounts, "total_amount": total, "screenshot_path": str(screenshot), "source": source},
        artifacts=[str(screenshot)],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="fund receivable-bill sum",
        data={
            "month": month,
            "field_name": RECEIVABLE_FIELD_NAME,
            "amounts": amounts,
            "total_amount": total,
            "screenshot_path": str(screenshot),
            "source": source,
            "scene": scene,
            "simulated": simulated,
            "dry_run": dry_run,
            "artifacts": [str(screenshot)],
            "context_path": str(context_path),
        },
    )


def run_promotion_balance_sum(*, screenshot_dir: str | Path, dry_run: bool = False) -> CommandResponse:
    inputs = {"screenshot_dir": str(screenshot_dir), "dry_run": dry_run}
    scene = f"{TMCS_SITE}/{PROMOTION_SCENE}"
    if dry_run:
        balances = dict(SIMULATED_PROMOTION_BALANCES)
        screenshot = _write_placeholder_screenshot(_ensure_screenshot_dir(screenshot_dir) / f"promotion_balance_{_timestamp()}.png")
        source = "simulated"
        simulated = True
    else:
        text, screenshot = _read_promotion_balance_page(screenshot_dir=screenshot_dir)
        balances = extract_promotion_balances_from_text(text)
        source = "page"
        simulated = False

    total = sum_promotion_balances(balances)
    context_path = write_runtime_context(
        task_name="tmcs_fund_promotion_balance_sum",
        status="success",
        inputs=inputs,
        outputs={"balances": balances, "total_amount": total, "screenshot_path": str(screenshot), "source": source},
        artifacts=[str(screenshot)],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="fund promotion-balance sum",
        data={
            "balances": balances,
            "total_amount": total,
            "screenshot_path": str(screenshot),
            "source": source,
            "scene": scene,
            "simulated": simulated,
            "dry_run": dry_run,
            "artifacts": [str(screenshot)],
            "context_path": str(context_path),
        },
    )
