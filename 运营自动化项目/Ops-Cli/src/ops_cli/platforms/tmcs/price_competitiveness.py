"""TMCS 价格竞争力商品查询（商品 → 价格竞争力 → 输入商品编码 → 查询）。

真实读取路径（页面 DOM 交互，非接口重放）：
  猫超首页 → 左侧菜单「商品价格力」→「价格竞争力」页「每日跟价商品」tab
  → 在「商品编码」输入框填入商品编码 → 点「查询」→ 读取结果表格。

页面结构（已用 9222 + 主浏览器双浏览器学习沉淀）：
- 价格竞争力页内嵌一个跨域 iframe，hostname=tbmc.portal.tmall.com，
  path 含 `mmc-price-ice3/ProductPriceForce`，Playwright 可跨域读取其文本。
- 「商品编码」输入框：iframe 内某 `input[type=text]`，祖先文本含「商品编码」，
  且不是日期选择器（placeholder=选择日期）。
- 「查询」按钮：iframe 内文本为「查询」的 button。
- 结果表格：每行「商品信息」列含 `ItemID:<商品编码>`，即商品编码 = ItemID。
- 查询命中 → 表格只剩匹配行（ItemID == 商品编码）；
  未命中 → 表格为空且出现「共 0 条 / 暂无数据」。

本层只负责「读取原始表格 + 逐行精确匹配商品编码」并输出统一 JSON。
是否存在（exists）由逐行 ItemID 精确匹配判定，绝不只看「有没有数据」。
dry-run 返回 simulated=True 的占位结果，不访问页面。
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.platforms.tmcs.shared import TMCS_SITE
from ops_cli.runtime_context import write_runtime_context


TMCS_PRICE_COMPETITIVENESS_SCENE = "price_competitiveness_lookup"

# 价格竞争力页内层业务 iframe（跨域）。
_PRICE_FORCE_INNER_URL = (
    "https://tbmc.portal.tmall.com/mmc-ice3-app/mmc-price-ice3/ProductPriceForce?bizTypeShop=MAOCHAO"
)
# 猫超后台用 ?frameUrl= 路由内层页面，整页导航地址。
TMCS_PRICE_COMPETITIVENESS_NAV_URL = (
    "https://web.txcs.tmall.com/?frameUrl=" + quote(_PRICE_FORCE_INNER_URL, safe="")
)
_PRICE_FORCE_FRAME_HOST = "tbmc.portal.tmall.com"
_PRICE_FORCE_FRAME_PATH_MARK = "ProductPriceForce"

_ITEM_ID_RE = re.compile(r"ItemID[:：]\s*(\d+)")
_SKU_ID_RE = re.compile(r"SkuID[:：]\s*(\d+)", re.IGNORECASE)
_TOTAL_RE = re.compile(r"共\s*(\d+)\s*条")
_EMPTY_MARKERS = ("暂无数据", "没有数据", "暂无符合", "无数据", "No Data")

# iframe 内读取表格行（商品编码=ItemID、SkuID、标题）与总条数 / 空态。
_READ_TABLE_JS = r"""
() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const items = [];
  for (const r of rows) {
    const txt = (r.innerText || '');
    const m = txt.match(/ItemID[:：]\s*(\d+)/);
    if (!m) continue;
    const sku = (txt.match(/SkuID[:：]\s*(\d+)/i) || [])[1] || null;
    const title = (txt.split('\n').map(s => s.trim()).filter(Boolean)[0] || '').slice(0, 80);
    items.push({item_id: m[1], sku_id: sku, title});
  }
  const all = document.body.innerText || '';
  const total = (all.match(/共\s*(\d+)\s*条/) || [])[1] || null;
  const empty = /暂无数据|没有数据|暂无符合|无数据|No Data/.test(all);
  return {items, total, empty, page_text_len: all.length};
}
"""

# 商品编码输入框：祖先文本含「商品编码」、非日期选择器的 text input。
_CODE_INPUT_XPATH = (
    "xpath=//*[contains(normalize-space(text()),'商品编码')]"
    "/following::input[not(@placeholder='选择日期')][1]"
)

# 整张列表读取：逐行 item_id/sku_id/title + 总条数 + 日期（每日跟价商品列表）。
_READ_ROWS_JS = r"""
() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const items = [];
  for (const r of rows) {
    const txt = (r.innerText || '');
    const m = txt.match(/ItemID[:：]\s*(\d+)/);
    if (!m) continue;
    const sku = (txt.match(/SkuID[:：]\s*(\d+)/i) || [])[1] || null;
    const title = (txt.split('\n').map(s => s.trim()).filter(Boolean)[0] || '').slice(0, 80);
    items.push({item_id: m[1], sku_id: sku, title});
  }
  const all = document.body.innerText || '';
  const total = (all.match(/共\s*(\d+)\s*条/) || [])[1] || null;
  const dateInput = document.querySelector("input[placeholder='选择日期']");
  return {items, total, date: dateInput ? dateInput.value : null};
}
"""

# 「每页显示」条数下拉触发器（Next/Fusion 分页组件，档位 10/20/50/100）。
_PAGE_SIZE_TRIGGER_XPATH = (
    "xpath=//*[contains(text(),'每页显示')]/following::*"
    "[contains(@class,'next-select') or contains(@class,'next-pagination-size')][1]"
)


# --------------------------------------------------------------------------- #
# 纯函数：解析 / 判定（便于单测，不依赖 Playwright）
# --------------------------------------------------------------------------- #


def extract_item_ids(text: str) -> list[str]:
    """从页面文本提取全部 ItemID（商品编码）。"""
    return _ITEM_ID_RE.findall(text or "")


def parse_total_rows(text: str) -> int | None:
    """解析「共 N 条」总条数；解析不到返回 None。"""
    match = _TOTAL_RE.search(text or "")
    return int(match.group(1)) if match else None


def is_empty_result(text: str) -> bool:
    """判断是否为空结果（暂无数据等）。"""
    return any(marker in (text or "") for marker in _EMPTY_MARKERS)


def evaluate_lookup(product_code: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """逐行精确匹配商品编码，返回 exists / matched_items / total_rows。

    规则（见模块 docstring）：
    - 仅当某行 ItemID 与 product_code 完全相等才算命中；
    - 表格为空、或只有其它商品编码，都判 exists=False。
    """
    code = str(product_code).strip()
    matched: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for row in rows or []:
        item_id = str(row.get("item_id") or "").strip()
        if item_id != code:
            continue
        key = (item_id, row.get("sku_id"))
        if key in seen:
            continue
        seen.add(key)
        matched.append(
            {
                "item_id": item_id,
                "sku_id": row.get("sku_id"),
                "title": row.get("title"),
            }
        )
    return {
        "exists": bool(matched),
        "matched_items": matched,
        "total_rows": len(rows or []),
    }


# --------------------------------------------------------------------------- #
# 真实读取：9222 + Playwright 页面 DOM 交互
# --------------------------------------------------------------------------- #


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _is_login_page(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    text = url.lower()
    path = (parsed.path or "").lower()
    return "login" in text or path.startswith("/member/login")


def _find_price_force_frame(page: Any) -> Any | None:
    """按 hostname + path 精确匹配内层业务 iframe（外层 URL 的 frameUrl 参数会带同名子串，
    必须用 hostname 判定，不能用整串 in）。"""
    for frame in page.frames:
        parsed = urlparse(frame.url)
        if parsed.hostname == _PRICE_FORCE_FRAME_HOST and _PRICE_FORCE_FRAME_PATH_MARK in (parsed.path or ""):
            return frame
    return None


def _import_chrome_and_playwright():
    """加载 SessionHub 9222 Chrome 与 Playwright 依赖（lookup / list 共用）。"""
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

    return CDP_URL, bring_chrome_to_front, PlaywrightError, sync_playwright


def _open_price_force_page(p, *, cdp_url, playwright_error, bring_to_front):
    """连 9222、导航价格竞争力页、做登录守卫、等内层 iframe，返回 (page, frame, created_page)。"""
    try:
        browser = p.chromium.connect_over_cdp(cdp_url)
    except playwright_error as exc:
        raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    existing_pages = context.pages
    created_page = not existing_pages
    page = existing_pages[0] if existing_pages else context.new_page()

    page.goto(TMCS_PRICE_COMPETITIVENESS_NAV_URL, wait_until="domcontentloaded", timeout=40000)
    if _is_login_page(page.url):
        bring_to_front()
        raise RuntimeError(
            "TMCS_LOGIN_REQUIRED：检测到猫超登录页，已切到前台，请先完成登录后重试。"
        )

    frame = None
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        frame = _find_price_force_frame(page)
        if frame is not None:
            break
        page.wait_for_timeout(1000)
    if frame is None:
        if _is_login_page(page.url):
            bring_to_front()
            raise RuntimeError("TMCS_LOGIN_REQUIRED：检测到猫超登录页，请先完成登录后重试。")
        raise RuntimeError(
            "PRICE_COMPETITIVENESS_PAGE_NOT_FOUND：未找到价格竞争力页内层 iframe，"
            "页面结构可能已变化，请在主浏览器重新核对入口。"
        )
    return page, frame, created_page


def _read_price_competitiveness(
    product_code: str,
    *,
    screenshot_dir: str | None = None,
) -> dict[str, Any]:
    """真实读取（单个实时查询）：填商品编码、点查询、读结果表格。"""
    cdp_url, bring_chrome_to_front, PlaywrightError, sync_playwright = _import_chrome_and_playwright()

    with sync_playwright() as p:
        page, frame, created_page = _open_price_force_page(
            p, cdp_url=cdp_url, playwright_error=PlaywrightError, bring_to_front=bring_chrome_to_front
        )
        try:
            # 等待输入框可用。
            code_input = frame.locator(_CODE_INPUT_XPATH).first
            try:
                code_input.wait_for(state="visible", timeout=20000)
            except PlaywrightError as exc:
                raise RuntimeError(
                    "PRICE_COMPETITIVENESS_INPUT_NOT_FOUND：未找到「商品编码」输入框。"
                ) from exc

            code = str(product_code).strip()
            code_input.click()
            code_input.fill("")
            code_input.fill(code)

            try:
                frame.get_by_role("button", name="查询").first.click(timeout=10000)
            except PlaywrightError as exc:
                raise RuntimeError(
                    "PRICE_COMPETITIVENESS_QUERY_BUTTON_NOT_FOUND：未找到「查询」按钮。"
                ) from exc

            # 轮询等待表格刷新到「过滤后」状态：要么为空，要么所有 ItemID 都等于查询编码。
            result: dict[str, Any] = {}
            query_deadline = time.monotonic() + 20
            while True:
                result = frame.evaluate(_READ_TABLE_JS)
                ids = [str(it.get("item_id")) for it in result.get("items") or []]
                settled = bool(result.get("empty")) or (
                    bool(ids) and all(i == code for i in ids)
                ) or (not ids and parse_total_rows_safe(result) == 0)
                if settled or time.monotonic() >= query_deadline:
                    break
                page.wait_for_timeout(1000)

            rows = result.get("items") or []
            verdict = evaluate_lookup(code, rows)
            total_text = result.get("total")
            verdict["total_rows"] = (
                int(total_text) if total_text not in (None, "") else len(rows)
            )

            screenshot_path = _maybe_screenshot(
                page=page,
                bring_to_front=bring_chrome_to_front,
                screenshot_dir=screenshot_dir,
                slug=code,
            )
            verdict["screenshot_path"] = screenshot_path
            verdict["empty"] = bool(result.get("empty"))
            return verdict
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass


def parse_total_rows_safe(result: dict[str, Any]) -> int | None:
    total = result.get("total")
    if total in (None, ""):
        return None
    try:
        return int(total)
    except (TypeError, ValueError):
        return None


def _maybe_screenshot(
    *,
    page: Any,
    bring_to_front: Callable[[], Any],
    screenshot_dir: str | None,
    slug: str,
) -> str | None:
    """仅当显式提供 screenshot_dir 时截图存证（9222 后台需先 bring_to_front，否则会挂死）。"""
    if not screenshot_dir:
        return None
    try:
        out_dir = Path(screenshot_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"price_competitiveness_{slug}.png"
        try:
            bring_to_front()
        except Exception:
            pass
        page.screenshot(path=str(target), timeout=15000)
        return str(target)
    except Exception:
        return None


def _set_page_size_max(frame: Any, page: Any, playwright_error: Any) -> None:
    """把「每页显示」设为最大档（优先 100），尽量一页取全。失败不致命（退回翻页采集）。"""
    try:
        trigger = frame.locator(_PAGE_SIZE_TRIGGER_XPATH).first
        if trigger.count() == 0:
            return
        trigger.click(timeout=8000)
        page.wait_for_timeout(800)
        for label in ("100", "50"):
            option = frame.locator(".next-menu-item, [role=option]").filter(
                has_text=re.compile(rf"^\s*{label}\s*$")
            ).first
            if option.count() > 0:
                option.click(timeout=5000)
                page.wait_for_timeout(2000)
                return
    except playwright_error:
        return


def _collect_all_rows(frame: Any, page: Any, playwright_error: Any) -> dict[str, Any]:
    """采集整张列表所有页的行（item_id/sku_id/title），按 (item_id, sku_id) 去重。"""
    seen: set[tuple[str, str | None]] = set()
    rows: list[dict[str, Any]] = []
    total: str | None = None
    list_date: str | None = None

    for _ in range(50):  # 安全上限：最多 50 页
        page.wait_for_timeout(300)
        data = frame.evaluate(_READ_ROWS_JS)
        if data.get("total") not in (None, ""):
            total = data.get("total")
        list_date = data.get("date") or list_date
        for item in data.get("items") or []:
            item_id = str(item.get("item_id") or "").strip()
            if not item_id:
                continue
            key = (item_id, item.get("sku_id"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {"item_id": item_id, "sku_id": item.get("sku_id"), "title": item.get("title")}
            )

        next_btn = frame.get_by_text("下一页", exact=True).first
        if next_btn.count() == 0:
            break
        try:
            disabled = next_btn.evaluate(
                "e => { const el = e.closest('li,button,a') || e;"
                " return el.getAttribute('aria-disabled') === 'true'"
                " || /disabled/.test(el.className || '') || el.disabled === true; }"
            )
        except playwright_error:
            break
        if disabled:
            break
        try:
            next_btn.click(timeout=5000)
        except playwright_error:
            break

    total_int = int(total) if total not in (None, "") else len(rows)
    return {"rows": rows, "total_rows": total_int, "list_date": list_date}


def _read_price_competitiveness_list(*, screenshot_dir: str | None = None) -> dict[str, Any]:
    """真实读取整张「每日跟价商品」列表：默认无过滤、设最大页大小、翻页采集全部行。"""
    cdp_url, bring_chrome_to_front, PlaywrightError, sync_playwright = _import_chrome_and_playwright()

    with sync_playwright() as p:
        page, frame, created_page = _open_price_force_page(
            p, cdp_url=cdp_url, playwright_error=PlaywrightError, bring_to_front=bring_chrome_to_front
        )
        try:
            # 等待表格首屏出现。
            try:
                frame.locator("table tbody tr").first.wait_for(state="attached", timeout=20000)
            except PlaywrightError as exc:
                raise RuntimeError(
                    "PRICE_COMPETITIVENESS_TABLE_NOT_FOUND：未读到价格竞争力结果表格。"
                ) from exc

            _set_page_size_max(frame, page, PlaywrightError)
            snapshot = _collect_all_rows(frame, page, PlaywrightError)
            snapshot["screenshot_path"] = _maybe_screenshot(
                page=page,
                bring_to_front=bring_chrome_to_front,
                screenshot_dir=screenshot_dir,
                slug="list",
            )
            return snapshot
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass


# --------------------------------------------------------------------------- #
# 命令入口
# --------------------------------------------------------------------------- #


def _scene_label() -> str:
    return f"{TMCS_SITE}/{TMCS_PRICE_COMPETITIVENESS_SCENE}"


def run_price_competitiveness_lookup(
    *,
    product_code: str,
    dry_run: bool = False,
    screenshot_dir: str | None = None,
) -> CommandResponse:
    code = str(product_code or "").strip()
    if not code:
        raise RuntimeError("PRODUCT_CODE_REQUIRED：缺少必填参数 --product-code（商品编码）。")

    inputs = {"product_code": code, "dry_run": dry_run, "screenshot_dir": screenshot_dir}
    scene = _scene_label()

    if dry_run:
        context_path = write_runtime_context(
            task_name="tmcs_price_competitiveness_lookup",
            status="success",
            inputs=inputs,
            outputs={"simulated": True, "product_code": code},
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="price-competitiveness lookup",
            data={
                "product_code": code,
                "exists": False,
                "matched_items": [],
                "total_rows": 0,
                "source": "simulated",
                "simulated": True,
                "scene": scene,
                "screenshot_path": None,
                "dry_run": True,
                "artifacts": [],
                "context_path": str(context_path),
            },
        )

    verdict = _read_price_competitiveness(code, screenshot_dir=screenshot_dir)
    context_path = write_runtime_context(
        task_name="tmcs_price_competitiveness_lookup",
        status="success",
        inputs=inputs,
        outputs={
            "product_code": code,
            "exists": verdict["exists"],
            "matched_items": verdict["matched_items"],
            "total_rows": verdict["total_rows"],
            "source": "page",
        },
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="price-competitiveness lookup",
        data={
            "product_code": code,
            "exists": verdict["exists"],
            "matched_items": verdict["matched_items"],
            "total_rows": verdict["total_rows"],
            "source": "page",
            "simulated": False,
            "scene": scene,
            "screenshot_path": verdict.get("screenshot_path"),
            "dry_run": False,
            "artifacts": [],
            "context_path": str(context_path),
        },
    )


def run_price_competitiveness_list(
    *,
    dry_run: bool = False,
    screenshot_dir: str | None = None,
) -> CommandResponse:
    """一次性导出整张「每日跟价商品」列表（供业务层缓存后批量匹配）。"""
    inputs = {"dry_run": dry_run, "screenshot_dir": screenshot_dir}
    scene = _scene_label()

    if dry_run:
        context_path = write_runtime_context(
            task_name="tmcs_price_competitiveness_list",
            status="success",
            inputs=inputs,
            outputs={"simulated": True, "total_rows": 0},
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="price-competitiveness list",
            data={
                "rows": [],
                "total_rows": 0,
                "list_date": None,
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "source": "simulated",
                "simulated": True,
                "scene": scene,
                "screenshot_path": None,
                "dry_run": True,
                "artifacts": [],
                "context_path": str(context_path),
            },
        )

    snapshot = _read_price_competitiveness_list(screenshot_dir=screenshot_dir)
    captured_at = datetime.now().isoformat(timespec="seconds")
    rows = snapshot["rows"]
    context_path = write_runtime_context(
        task_name="tmcs_price_competitiveness_list",
        status="success",
        inputs=inputs,
        outputs={
            "total_rows": snapshot["total_rows"],
            "row_count": len(rows),
            "list_date": snapshot.get("list_date"),
            "source": "page",
        },
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="price-competitiveness list",
        data={
            "rows": rows,
            "total_rows": snapshot["total_rows"],
            "list_date": snapshot.get("list_date"),
            "captured_at": captured_at,
            "source": "page",
            "simulated": False,
            "scene": scene,
            "screenshot_path": snapshot.get("screenshot_path"),
            "dry_run": False,
            "artifacts": [],
            "context_path": str(context_path),
        },
    )


def learn_price_competitiveness_lookup(*, force: bool = False) -> CommandResponse:
    inputs = {"site": TMCS_SITE, "scene": TMCS_PRICE_COMPETITIVENESS_SCENE, "force": force}
    note = (
        "价格竞争力查询走 9222 + Playwright 直接读取「价格竞争力」页内层 iframe（page DOM 交互），"
        "无需额外捕获 scene。真实路径：首页 → 商品价格力 →「价格竞争力」→ 每日跟价商品 →"
        " 商品编码输入框 → 查询 → 结果表格（商品编码 = ItemID）。"
    )
    context_path = write_runtime_context(
        task_name="tmcs_price_competitiveness_learn",
        status="success",
        inputs=inputs,
        outputs={"site": TMCS_SITE, "scene": TMCS_PRICE_COMPETITIVENESS_SCENE, "note": note},
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="price-competitiveness learn",
        data={
            "site": TMCS_SITE,
            "scene": TMCS_PRICE_COMPETITIVENESS_SCENE,
            "mode": "page_dom",
            "note": note,
            "next_command": "ops --json tmcs price-competitiveness lookup --product-code <商品编码>",
            "context_path": str(context_path),
        },
    )
