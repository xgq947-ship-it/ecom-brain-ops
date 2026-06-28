"""TMCS XP 工单数量读取。

真实模式直接读取猫超首页可见文本，从首页待办卡片提取：
`XP工单处理 紧急(4)`

dry-run：完全跳过浏览器读取，返回 simulated=True 的占位结果。
"""

from __future__ import annotations

import re
import sys
from urllib.parse import urlparse
from pathlib import Path

from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.platforms.tmcs.shared import (
    TMCS_SITE,
    TMCS_XP_WORKORDER_COUNT_SCENE,
)
from ops_cli.runtime_context import write_runtime_context


DEFAULT_THRESHOLD = 4
TMCS_HOME_URL = "https://web.txcs.tmall.com/"
_XP_WORKORDER_PATTERN = re.compile(r"XP\s*工单处理\s*紧急\((\d+)\)")


def extract_workorder_count(text: str) -> int | None:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    match = _XP_WORKORDER_PATTERN.search(normalized)
    if not match:
        return None
    return int(match.group(1))


def _is_login_page(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    text = url.lower()
    path = (parsed.path or "").lower()
    return "login" in text or path.startswith("/member/login")


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _read_homepage_text() -> str:
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
            page.goto(TMCS_HOME_URL, wait_until="domcontentloaded", timeout=30000)
            deadline_ms = 15000
            step_ms = 1000
            waited_ms = 0
            while True:
                if _is_login_page(page.url):
                    bring_chrome_to_front()
                    raise RuntimeError("TMCS_LOGIN_REQUIRED：检测到猫超登录页，已切到前台，请先完成登录后重试。")
                text = page.locator("body").inner_text(timeout=10000)
                if extract_workorder_count(text) is not None:
                    return text
                if waited_ms >= deadline_ms:
                    raise RuntimeError(
                        "WORKORDER_COUNT_NOT_FOUND：猫超首页未找到 `XP工单处理 紧急(n)` 文本。"
                    )
                page.wait_for_timeout(step_ms)
                waited_ms += step_ms
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass


def count_xp_workorders(
    *,
    threshold: int = DEFAULT_THRESHOLD,
    dry_run: bool = False,
) -> CommandResponse:
    inputs = {"threshold": threshold, "dry_run": dry_run}

    if dry_run:
        context_path = write_runtime_context(
            task_name="tmcs_xp_workorder_count",
            status="success",
            inputs=inputs,
            outputs={"simulated": True, "count": 0, "threshold": threshold},
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="xp-workorder count",
            data={
                "count": 0,
                "threshold": threshold,
                "exceeded": False,
                "source": "simulated",
                "simulated": True,
                "scene": f"{TMCS_SITE}/{TMCS_XP_WORKORDER_COUNT_SCENE}",
                "dry_run": True,
                "context_path": str(context_path),
            },
        )

    homepage_text = _read_homepage_text()
    count = extract_workorder_count(homepage_text)
    if count is None:
        raise RuntimeError("WORKORDER_COUNT_NOT_FOUND：猫超首页未找到 XP 工单处理紧急数量。")

    exceeded = count > threshold
    context_path = write_runtime_context(
        task_name="tmcs_xp_workorder_count",
        status="success",
        inputs=inputs,
        outputs={"count": count, "threshold": threshold, "exceeded": exceeded, "source": "dom"},
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="xp-workorder count",
        data={
            "count": count,
            "threshold": threshold,
            "exceeded": exceeded,
            "source": "dom",
            "simulated": False,
            "scene": f"{TMCS_SITE}/{TMCS_XP_WORKORDER_COUNT_SCENE}",
            "dry_run": False,
            "context_path": str(context_path),
        },
    )


def learn_xp_workorder_count(*, force: bool = False) -> CommandResponse:
    inputs = {"site": TMCS_SITE, "scene": TMCS_XP_WORKORDER_COUNT_SCENE, "force": force}
    context_path = write_runtime_context(
        task_name="tmcs_xp_workorder_learn",
        status="success",
        inputs=inputs,
        outputs={
            "site": TMCS_SITE,
            "scene": TMCS_XP_WORKORDER_COUNT_SCENE,
            "mode": "homepage_dom",
            "note": "XP 工单监控已改为直接读取猫超首页 DOM 文本，无需 scene 学习。",
        },
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="xp-workorder learn",
        data={
            "site": TMCS_SITE,
            "scene": TMCS_XP_WORKORDER_COUNT_SCENE,
            "mode": "homepage_dom",
            "note": "XP 工单监控已改为直接读取猫超首页 DOM 文本，无需 scene 学习。",
            "next_command": "ops --json tmcs xp-workorder count",
            "context_path": str(context_path),
        },
    )
