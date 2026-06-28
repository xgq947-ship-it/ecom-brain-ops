from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ops_cli.output import CommandResponse


JST_FALLBACK_URL = "https://www.erp321.com/epaas"
PROFILE_DIR = Path("runtime/browser")


def _profile_path(scene: str) -> Path:
    safe_scene = scene.replace("/", "-").replace(" ", "-")
    return Path.cwd() / PROFILE_DIR / f"jst_{safe_scene}.json"


def _storage_script(storage_name: str) -> str:
    return f"""
    () => {{
      const data = {{}};
      for (let i = 0; i < window.{storage_name}.length; i++) {{
        const key = window.{storage_name}.key(i);
        data[key] = window.{storage_name}.getItem(key);
      }}
      return data;
    }}
    """


def _capture_selectors(page: Any) -> dict[str, Any]:
    return page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
          };
          const texts = [...document.querySelectorAll('button,a,span,div,label')]
            .filter(visible)
            .map(el => (el.innerText || el.textContent || '').trim())
            .filter(Boolean)
            .slice(0, 500);
          const inputs = [...document.querySelectorAll('input,textarea')]
            .filter(visible)
            .map(el => ({
              tag: el.tagName.toLowerCase(),
              type: el.getAttribute('type') || '',
              placeholder: el.getAttribute('placeholder') || '',
              ariaLabel: el.getAttribute('aria-label') || '',
              name: el.getAttribute('name') || ''
            }));
          return {visible_texts: texts, inputs};
        }
        """
    )


def learn_jst_browser_scene(*, scene: str, timeout: int = 90, cdp_url: str | None = None) -> CommandResponse:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore

    resolved_cdp_url = cdp_url or os.environ.get("PRIMARY_CHROME_CDP_URL")
    if not resolved_cdp_url:
        raise RuntimeError(
            "缺少主浏览器学习入口。请通过 --cdp-url 或 PRIMARY_CHROME_CDP_URL 传入普通主浏览器的 CDP 地址；9222 只用于正式执行。"
        )
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(resolved_cdp_url)
        except PlaywrightError as exc:
            raise RuntimeError(f"无法连接主浏览器 CDP：{resolved_cdp_url}。请先确认主浏览器探测入口可用。原始错误：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        if not page.url or page.url == "about:blank":
            page.goto(JST_FALLBACK_URL, wait_until="domcontentloaded", timeout=30000)

        deadline = time.time() + timeout
        while time.time() < deadline:
            page.wait_for_timeout(1000)

        profile = {
            "site": "jst_erp",
            "scene": scene,
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "source": "primary_chrome_manual_page_flow",
            "cdp_url": resolved_cdp_url,
            "page_url": page.url,
            "page_title": page.title(),
            "cookies": context.cookies(),
            "local_storage": page.evaluate(_storage_script("localStorage")),
            "session_storage": page.evaluate(_storage_script("sessionStorage")),
            "selectors": _capture_selectors(page),
            "notes": {
                "boundary": "JST import uses browser page automation only; no JST import API is captured or replayed.",
                "manual_flow": "商品 -> 店铺商品管理 -> 店铺商品信息 -> 导入 -> 导入店铺商品资料",
            },
        }
        path = _profile_path(scene)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    return CommandResponse(
        success=True,
        platform="jst",
        command="browser learn",
        data={
            "scene": scene,
            "profile_path": str(path),
            "page_url": profile["page_url"],
            "page_title": profile["page_title"],
            "next_command": "ops jst shop-goods import --file /path/to/import.xlsx --shop-name '（猫超）福安市启明工贸有限公司（肖国清）' --mode cover --output json",
        },
    )


def load_jst_browser_profile(scene: str) -> dict[str, Any]:
    path = _profile_path(scene)
    if not path.exists():
        raise FileNotFoundError(f"未找到 JST 浏览器学习文件：{path}。请先运行 `ops jst browser learn --scene {scene}`。")
    return json.loads(path.read_text(encoding="utf-8"))
