"""TMCS 营销端「风险预警（N）」数值读取。

读取路径（真实模式）：天猫超市 → 营销 → （新）营销活动中心 → 风险预警（N）。

本层只负责"读取原始数值"并输出统一 JSON：
- risk_warning_count：风险预警数值（整数）
- label_text：页面原始文本，如「风险预警（0）」

是否需要预警、是否要通知，全部交给业务层 workflow 判断；本层不做阈值比较，
也绝不处理/关闭预警。

真实模式用 SessionHub 9222 + Playwright 进入营销活动中心页，逐帧读取渲染文本，
再用 `extract_risk_warning_count` 解析「风险预警（N）」。
dry-run 返回 simulated=True 的占位结果，不访问页面。

注意：营销活动中心的 frameUrl 路由可能随平台改版变化。若真实读取失败，请人工在
主浏览器打开「（新）营销活动中心」核对入口，并按需更新本文件 URL 或沉淀 scene
`tmall_chaoshi/marketing_risk_warning_count`。
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.platforms.tmcs.shared import (
    TMCS_MARKETING_RISK_WARNING_SCENE,
    TMCS_SITE,
)
from ops_cli.runtime_context import write_runtime_context


TMCS_HOME_URL = "https://web.txcs.tmall.com/"
# （新）营销活动中心页的 frame 路由地址（已用双浏览器学习核对，2026-06）。
# 路径：天猫超市 → 营销 →（新）营销活动中心；「重要事项」卡片含「风险预警 N」徽标。
TMCS_MARKETING_CENTER_FRAME_URL = (
    "https://web.txcs.tmall.com/?frameUrl="
    "https%3A%2F%2Ftxcs.portal.tmall.com%2Fmmc-market-across"
    "%2Fpages%2FTmallActivityHomePage%2Findex.html"
)

# 「风险预警」后紧跟数值：真实页面是「风险预警」标签 + 数字徽标（DOM 文本形如
# "风险预警\n0"），也兼容「风险预警（N）」全角/半角括号写法。
_RISK_WARNING_PATTERN = re.compile(r"风险预警\s*[（(]?\s*(\d+)")


def extract_risk_warning_count(text: str) -> int | None:
    """从页面文本提取「风险预警」后的数值 N。找不到返回 None。

    兼容两种渲染：徽标形式「风险预警 0」与括号形式「风险预警（0）」。
    """
    normalized = re.sub(r"\s+", " ", text or "").strip()
    match = _RISK_WARNING_PATTERN.search(normalized)
    if not match:
        return None
    return int(match.group(1))


# 「重要事项」卡片为空时平台只渲染「暂无数据」、不再列出「风险预警（0）」一项
# （2026-06 实测：0 项时旧版显示「风险预警（0）」，改版后空卡片显示「暂无数据」）。
_EMPTY_IMPORTANT_PATTERN = re.compile(r"重要事项.{0,20}暂无数据")


def _important_items_empty(text: str) -> bool:
    """页面确实在营销活动中心、「重要事项」卡片为空（暂无数据）且全文无「风险预警」时为 True。

    仅在能正向确认「空状态」时返回 True；页面没加载到营销活动中心则不算空，避免把
    加载失败误判成 0。
    """
    normalized = re.sub(r"\s+", " ", text or "")
    return (
        "营销活动中心" in normalized
        and "风险预警" not in normalized
        and bool(_EMPTY_IMPORTANT_PATTERN.search(normalized))
    )


def resolve_risk_warning_count(text: str) -> int | None:
    """解析风险预警数值：优先取「风险预警（N）」徽标；卡片空（暂无数据）按 0 处理；否则 None。"""
    count = extract_risk_warning_count(text)
    if count is not None:
        return count
    if _important_items_empty(text):
        return 0
    return None


def _normalize_label(text: str) -> str:
    """返回归一后的「风险预警（N）」标签文本（全角括号），找不到返回空串。"""
    count = resolve_risk_warning_count(text)
    if count is None:
        return ""
    return f"风险预警（{count}）"


def _save_failure_evidence(page, text: str, *, headless: bool, bring_to_front) -> str:
    """风险预警读取失败时落盘现场（页面文本 + 截图），便于离线诊断页面是否改版。

    纯诊断，不影响主流程；任何异常都吞掉，返回证据文件路径（失败则空串）。
    """
    try:
        debug_dir = Path(get_config().runtime_dir) / "context"
        debug_dir.mkdir(parents=True, exist_ok=True)
        base = debug_dir / f"risk_warning_fail_{datetime.now():%Y%m%d_%H%M%S}"
        try:
            url = page.url
        except Exception:  # noqa: BLE001
            url = ""
        base.with_suffix(".txt").write_text(
            f"url={url}\n含「风险预警」文本={'风险预警' in (text or '')}\n\n{text or ''}",
            encoding="utf-8",
        )
        try:
            if not headless:
                bring_to_front()  # 前台窗口截图前置顶，规避 9222 后台截图挂死
            page.screenshot(path=str(base.with_suffix(".png")), timeout=8000)
        except Exception:  # noqa: BLE001 - 截图失败不影响文本证据
            pass
        return str(base.with_suffix(".txt"))
    except Exception:  # noqa: BLE001
        return ""


def _is_login_page(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    lower = url.lower()
    path = (parsed.path or "").lower()
    return "login" in lower or path.startswith("/member/login")


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _read_marketing_page_text() -> str:
    """真实读取：SessionHub 9222 + Playwright 进入营销活动中心页，逐帧读取文本。"""
    root = _sessionhub_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from scene.chrome_cdp import (  # type: ignore
            CDP_URL,
            bring_chrome_to_front,
            foreground_allowed,
            start_chrome,
        )
    except Exception as exc:  # pragma: no cover - import path guard
        raise RuntimeError(f"无法加载 SessionHub Chrome 依赖：{exc}") from exc

    use_headless = not foreground_allowed()
    ok, msg = start_chrome(headless=use_headless)
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
            page.goto(TMCS_MARKETING_CENTER_FRAME_URL, wait_until="domcontentloaded", timeout=30000)
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

            deadline_ms = 20000
            step_ms = 1500
            waited_ms = 0
            while True:
                if _is_login_page(page.url):
                    bring_chrome_to_front()
                    raise RuntimeError("TMCS_LOGIN_REQUIRED：检测到猫超登录页，已切到前台，请先完成登录后重试。")
                text = _all_frames_text()
                if resolve_risk_warning_count(text) is not None:
                    return text
                if waited_ms >= deadline_ms:
                    evidence = _save_failure_evidence(
                        page, text, headless=use_headless, bring_to_front=bring_chrome_to_front
                    )
                    has_label = "风险预警" in (text or "")
                    diag = (
                        "页面有「风险预警」但其后未跟数字徽标——可能徽标渲染/DOM 结构变了，需调整解析"
                        if has_label
                        else "页面完全没有「风险预警」文本——可能入口已改版或「重要事项」卡片未加载，需重核 frameUrl / 重沉淀 scene"
                    )
                    raise RuntimeError(
                        "RISK_WARNING_COUNT_NOT_FOUND：营销活动中心页未找到「风险预警（N）」文本。"
                        f"诊断：{diag}。" + (f"现场已存：{evidence}。" if evidence else "")
                        + "请人工核对营销活动中心入口或沉淀 scene tmall_chaoshi/marketing_risk_warning_count。"
                    )
                page.wait_for_timeout(step_ms)
                waited_ms += step_ms
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass


def run_marketing_risk_warning_count(*, dry_run: bool = False) -> CommandResponse:
    inputs = {"dry_run": dry_run}
    scene = f"{TMCS_SITE}/{TMCS_MARKETING_RISK_WARNING_SCENE}"

    if dry_run:
        label_text = "风险预警（0）"
        context_path = write_runtime_context(
            task_name="tmcs_marketing_risk_warning_count",
            status="success",
            inputs=inputs,
            outputs={"simulated": True, "risk_warning_count": 0, "label_text": label_text},
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="marketing risk-warning count",
            data={
                "risk_warning_count": 0,
                "label_text": label_text,
                "source": "simulated",
                "simulated": True,
                "scene": scene,
                "dry_run": True,
                "artifacts": [],
                "context_path": str(context_path),
            },
        )

    page_text = _read_marketing_page_text()
    count = resolve_risk_warning_count(page_text)
    if count is None:
        raise RuntimeError(
            "RISK_WARNING_COUNT_NOT_FOUND：营销活动中心页未解析到「风险预警（N）」数值。"
        )

    # 徽标命中即 page；卡片空（暂无数据）按 0 处理，标注来源便于排查。
    source = "page" if extract_risk_warning_count(page_text) is not None else "empty_important_items"
    label_text = _normalize_label(page_text)
    context_path = write_runtime_context(
        task_name="tmcs_marketing_risk_warning_count",
        status="success",
        inputs=inputs,
        outputs={"risk_warning_count": count, "label_text": label_text, "source": source},
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="marketing risk-warning count",
        data={
            "risk_warning_count": count,
            "label_text": label_text,
            "source": source,
            "simulated": False,
            "scene": scene,
            "dry_run": False,
            "artifacts": [],
            "context_path": str(context_path),
        },
    )


def learn_marketing_risk_warning_count(*, force: bool = False) -> CommandResponse:
    inputs = {"site": TMCS_SITE, "scene": TMCS_MARKETING_RISK_WARNING_SCENE, "force": force}
    note = (
        "营销端风险预警读取走 9222 + Playwright 进入「（新）营销活动中心」页，逐帧读取渲染文本，"
        "再解析「风险预警（N）」。营销活动中心 frameUrl 可能随平台改版变化；如真实读取失败，"
        "请在主浏览器打开「营销 →（新）营销活动中心」核对入口，并按需更新 URL 或沉淀 scene。"
    )
    context_path = write_runtime_context(
        task_name="tmcs_marketing_risk_warning_learn",
        status="success",
        inputs=inputs,
        outputs={
            "site": TMCS_SITE,
            "scene": TMCS_MARKETING_RISK_WARNING_SCENE,
            "mode": "page_dom",
            "note": note,
        },
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="marketing risk-warning learn",
        data={
            "site": TMCS_SITE,
            "scene": TMCS_MARKETING_RISK_WARNING_SCENE,
            "mode": "page_dom",
            "note": note,
            "next_command": "ops --json tmcs marketing risk-warning count",
            "context_path": str(context_path),
        },
    )
