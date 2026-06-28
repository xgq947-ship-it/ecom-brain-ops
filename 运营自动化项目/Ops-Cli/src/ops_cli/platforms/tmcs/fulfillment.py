"""TMCS 物流履约数据概览读取。

读取路径（真实模式）：猫超首页 → 商仓履约（天机）→ 物流履约 → 日常考核 → 数据概览。

本层只负责"读取原始数值"并输出统一 JSON：
- 考核 / 观测指标的原始值（按真实页面口径，含 4CP 占比，不是 7CP）
- 页面「考核表现」横幅给出的周数据预警等级（A/B/C 或 null）与心智仓判定

指标是否合格、是否接近预警、是否要通知，全部交给业务层 workflow 判断，
本层不做任何阈值比较。

真实模式用 SessionHub 9222 + Playwright 导航到日常考核页，读取渲染出的 BI iframe
文本（Playwright 可跨域读取 iframe 文本），再用 `extract_metrics_from_text` 解析。
dry-run 返回 simulated=True 的占位指标，不访问页面。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.platforms.tmcs.shared import (
    TMCS_FULFILLMENT_OVERVIEW_SCENE,
    TMCS_SITE,
)
from ops_cli.runtime_context import write_runtime_context


TMCS_HOME_URL = "https://web.txcs.tmall.com/"
# 物流履约（商仓履约/天机）页的 frame 路由地址。
TMCS_FULFILLMENT_FRAME_URL = (
    "https://web.txcs.tmall.com/?frameUrl="
    "https%3A%2F%2Fweb.txcs.tmall.com%2Fpages%2Fchaoshi%2Fai_tj_ly_mc"
)

# 8 项指标键名（按真实「日常考核」页口径）。
METRIC_KEYS: tuple[str, ...] = (
    "pickup_24h_rate",
    "pickup_48h_rate",
    "door_delivery_rate",
    "next_day_delivery_rate",
    "four_cp_rate",
    "four_cp_rate_ex_remote",
    "delivery_promise_rate",
    "avg_pay_to_sign_hours",
    "exception_feedback_required",
)

_NUMERIC_METRIC_KEYS: tuple[str, ...] = (
    "pickup_24h_rate",
    "pickup_48h_rate",
    "door_delivery_rate",
    "next_day_delivery_rate",
    "four_cp_rate",
    "four_cp_rate_ex_remote",
    "delivery_promise_rate",
    "avg_pay_to_sign_hours",
)

# 页面文本标签 -> 指标键。带括号/后缀的标签按真实页面书写。
_TEXT_LABELS: tuple[tuple[str, str], ...] = (
    ("24H支揽率", "pickup_24h_rate"),
    ("48H支揽率", "pickup_48h_rate"),
    ("送货上门率", "door_delivery_rate"),
    ("隔日达率", "next_day_delivery_rate"),
    ("4CP占比_剔偏远", "four_cp_rate_ex_remote"),
    ("4CP占比", "four_cp_rate"),
    ("表达签准率", "delivery_promise_rate"),
    ("支签时长", "avg_pay_to_sign_hours"),
)

# dry-run 占位：一组贴近真实口径、整体达标的模拟指标。
SIMULATED_METRICS: dict[str, Any] = {
    "pickup_24h_rate": 99.43,
    "pickup_48h_rate": 100.00,
    "door_delivery_rate": 92.59,
    "next_day_delivery_rate": 58.0,
    "four_cp_rate": 99.47,
    "four_cp_rate_ex_remote": 99.46,
    "delivery_promise_rate": 100.00,
    "avg_pay_to_sign_hours": 55.9,
    "exception_feedback_required": False,
}


def parse_fulfillment_metrics(raw: Any) -> dict[str, Any]:
    """把已抽取的指标 dict 归一成统一结构（纯函数，便于单测）。

    缺少全部数值指标时抛 FULFILLMENT_OVERVIEW_NOT_FOUND；个别指标缺失允许为 None
    （如「表达签准率/支签时长」不在日常考核默认卡片时）。
    """
    if not isinstance(raw, dict):
        raise RuntimeError(
            "FULFILLMENT_OVERVIEW_NOT_FOUND：履约数据概览原始数据结构不是对象。"
        )

    metrics: dict[str, Any] = {}
    for key in _NUMERIC_METRIC_KEYS:
        value = raw.get(key)
        if value is None:
            metrics[key] = None
            continue
        try:
            metrics[key] = round(float(value), 2)
        except (TypeError, ValueError):
            metrics[key] = None

    if all(metrics[key] is None for key in _NUMERIC_METRIC_KEYS):
        raise RuntimeError(
            "FULFILLMENT_OVERVIEW_NOT_FOUND：履约数据概览未解析到任何指标数值。"
        )

    metrics["exception_feedback_required"] = bool(raw.get("exception_feedback_required", False))
    return metrics


def extract_weekly_warning_level(text: str) -> str | None:
    """从「考核表现」横幅文本提取周数据预警等级 A/B/C。"""
    match = re.search(r"([ABC])\s*类\s*(?:预警|警告)", text or "")
    return match.group(1) if match else None


def extract_metrics_from_text(text: str) -> tuple[dict[str, Any], str | None]:
    """从日常考核页渲染文本解析指标 + 周预警等级。

    数值统一要求带小数点（如 99.43 / 100.00 / 55.9），可避开「（T+2）」中的整数；
    解析不到任何数值时抛 FULFILLMENT_OVERVIEW_NOT_FOUND。
    """
    normalized = re.sub(r"\s+", " ", text or "")
    # 「考核表现」横幅里也会出现指标名（如"请关注送货上门率…24H支揽率…"），
    # 会污染标签匹配；指标只在「数据概览」之后的区段解析。
    overview_idx = normalized.find("数据概览")
    metrics_text = normalized[overview_idx:] if overview_idx >= 0 else normalized

    raw: dict[str, Any] = {}
    for label, key in _TEXT_LABELS:
        if key in raw:
            continue
        # 4CP占比 不匹配 4CP占比_剔偏远
        guard = r"(?!_)" if label == "4CP占比" else ""
        if key == "avg_pay_to_sign_hours":
            # 支签时长(小时)：数值不带 %，限定标签后 ~12 个非数字字符内的小数。
            pattern = re.escape(label) + guard + r"[^0-9]{0,12}?(\d+\.\d+)"
        else:
            # 卡片数值形如"<标签>（…） 99.43 %"：限定标签后短窗口内、且紧跟 % 的小数，
            # 以排除「指标管理」列表里只有标签没有数值的干扰项。
            pattern = re.escape(label) + guard + r"[^%]{0,12}?(\d+\.\d+)\s*%"
        m = re.search(pattern, metrics_text)
        if m:
            raw[key] = m.group(1)

    # 异常单据数量 -> 是否需要反馈
    exc = re.search(r"异常单据[:：]?\s*(\d+)", metrics_text)
    raw["exception_feedback_required"] = bool(exc and int(exc.group(1)) > 0)

    metrics = parse_fulfillment_metrics(raw)
    return metrics, extract_weekly_warning_level(normalized)


def extract_metrics_from_page_text(
    *,
    get_blob: Callable[[], str],
    activate_daily_assess: Callable[[], None],
    wait: Callable[[int], None],
    refresh: Callable[[], None],
    attempts: int = 2,
    deadline_ms: int = 30000,
    step_ms: int = 1500,
) -> tuple[dict[str, Any], str | None]:
    """从页面文本读取履约数据；未出数时自动刷新重试一次。"""
    last_error: RuntimeError | None = None

    for attempt_index in range(attempts):
        waited_ms = 0
        activate_daily_assess()
        while True:
            blob = get_blob()
            try:
                # 「考核表现」只在日常考核视图出现，用它确认已切到正确视图。
                if "考核表现" in blob and "支揽率" in blob:
                    return extract_metrics_from_text(blob)
                if waited_ms >= deadline_ms:
                    if "支揽率" in blob:
                        # 兜底：已读到履约指标但未确认考核视图，仍按现有文本解析。
                        return extract_metrics_from_text(blob)
                    raise RuntimeError(
                        "FULFILLMENT_OVERVIEW_NOT_FOUND：未在日常考核页找到「考核表现 / 支揽率」数据概览文本。"
                    )
            except RuntimeError as exc:
                last_error = exc
                if "FULFILLMENT_OVERVIEW_NOT_FOUND" not in str(exc):
                    raise
                break

            activate_daily_assess()
            wait(step_ms)
            waited_ms += step_ms

        if attempt_index + 1 >= attempts:
            assert last_error is not None
            raise last_error
        refresh()

    assert last_error is not None
    raise last_error


def _is_login_page(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    text = url.lower()
    path = (parsed.path or "").lower()
    return "login" in text or path.startswith("/member/login")


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _read_fulfillment_overview() -> tuple[dict[str, Any], str | None]:
    """真实读取：SessionHub 9222 + Playwright 导航到日常考核页，读取 BI iframe 文本。"""
    root = _sessionhub_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from scene.chrome_cdp import CDP_URL, bring_chrome_to_front, start_chrome  # type: ignore
    except Exception as exc:  # pragma: no cover - import path guard
        raise RuntimeError(f"无法加载 SessionHub Chrome 依赖：{exc}") from exc

    ok, msg = start_chrome()
    if not ok:
        raise RuntimeError(msg)

    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("缺少 Playwright，请先运行：pip install -r requirements.txt") from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        existing_pages = context.pages
        created_page = not existing_pages
        page = existing_pages[0] if existing_pages else context.new_page()
        try:
            page.goto(TMCS_FULFILLMENT_FRAME_URL, wait_until="domcontentloaded", timeout=30000)
            if _is_login_page(page.url):
                bring_chrome_to_front()
                raise RuntimeError("TMCS_LOGIN_REQUIRED：检测到猫超登录页，已切到前台，请先完成登录后重试。")

            def _all_frames_text() -> str:
                parts: list[str] = []
                for frame in page.frames:
                    try:
                        parts.append(frame.locator("body").inner_text(timeout=2000))
                    except PlaywrightError:
                        continue
                return "\n".join(parts)

            def _click_daily_assess() -> None:
                # 「日/周/月/日常考核」tab 可能在主帧或同源子帧，逐帧尝试点击。
                for frame in page.frames:
                    try:
                        loc = frame.get_by_text("日常考核", exact=True).first
                        if loc.count() > 0:
                            loc.click(timeout=5000)
                            return
                    except PlaywrightError:
                        continue

            def _refresh_page() -> None:
                page.reload(wait_until="domcontentloaded", timeout=30000)
                if _is_login_page(page.url):
                    bring_chrome_to_front()
                    raise RuntimeError("TMCS_LOGIN_REQUIRED：检测到猫超登录页，已切到前台，请先完成登录后重试。")

            return extract_metrics_from_page_text(
                get_blob=_all_frames_text,
                activate_daily_assess=_click_daily_assess,
                wait=page.wait_for_timeout,
                refresh=_refresh_page,
            )
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass


def run_fulfillment_overview(*, dry_run: bool = False) -> CommandResponse:
    inputs = {"dry_run": dry_run}
    scene = f"{TMCS_SITE}/{TMCS_FULFILLMENT_OVERVIEW_SCENE}"

    if dry_run:
        metrics = dict(SIMULATED_METRICS)
        context_path = write_runtime_context(
            task_name="tmcs_fulfillment_overview",
            status="success",
            inputs=inputs,
            outputs={"simulated": True, "metrics": metrics},
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="fulfillment overview",
            data={
                "metrics": metrics,
                "weekly_warning_level": None,
                "source": "simulated",
                "simulated": True,
                "scene": scene,
                "dry_run": True,
                "artifacts": [],
                "context_path": str(context_path),
            },
        )

    metrics, weekly_warning_level = _read_fulfillment_overview()
    context_path = write_runtime_context(
        task_name="tmcs_fulfillment_overview",
        status="success",
        inputs=inputs,
        outputs={"metrics": metrics, "weekly_warning_level": weekly_warning_level, "source": "page"},
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="fulfillment overview",
        data={
            "metrics": metrics,
            "weekly_warning_level": weekly_warning_level,
            "source": "page",
            "simulated": False,
            "scene": scene,
            "dry_run": False,
            "artifacts": [],
            "context_path": str(context_path),
        },
    )


def learn_fulfillment_overview(*, force: bool = False) -> CommandResponse:
    inputs = {"site": TMCS_SITE, "scene": TMCS_FULFILLMENT_OVERVIEW_SCENE, "force": force}
    note = (
        "履约数据概览已改为 9222 + Playwright 直接读取「日常考核」页渲染文本，无需额外学习 scene。"
        "真实读取路径：首页 → 商仓履约（天机）→ 物流履约 → 日常考核 → 数据概览。"
    )
    context_path = write_runtime_context(
        task_name="tmcs_fulfillment_learn",
        status="success",
        inputs=inputs,
        outputs={"site": TMCS_SITE, "scene": TMCS_FULFILLMENT_OVERVIEW_SCENE, "note": note},
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="fulfillment learn",
        data={
            "site": TMCS_SITE,
            "scene": TMCS_FULFILLMENT_OVERVIEW_SCENE,
            "mode": "page_dom",
            "note": note,
            "next_command": "ops --json tmcs fulfillment overview",
            "context_path": str(context_path),
        },
    )
