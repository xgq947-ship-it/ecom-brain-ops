from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ops_cli.integrations.sessionhub import get_scene_manager
from ops_cli.output import CommandResponse
from ops_cli.platforms.jst.browser import JST_FALLBACK_URL
from ops_cli.platforms.jst.browser import load_jst_browser_profile
from ops_cli.platforms.jst.shared import surface_jst_login_if_needed


SESSIONHUB_CDP_URL = "http://127.0.0.1:9222"
SESSIONHUB_CHROME_COMMAND = (
    "/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome "
    "--remote-debugging-port=9222 "
    "--user-data-dir=/tmp/chrome-jst-import"
)
DEFAULT_SCENE = "shop-goods-import"
JST_SITE = "jst_erp"
JST_AUTH_SCENE = "order_list"
SCREENSHOT_DIR = Path("runtime/screenshots")
MODE_ALIASES = {
    "ignore": {"ignore", "忽略"},
    "cover": {"cover", "overwrite", "覆盖"},
}


def _screenshot(page: Any, prefix: str) -> str:
    path = Path.cwd() / SCREENSHOT_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def _safe_body_text(frame: Any) -> str:
    try:
        return frame.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def _find_frame(page: Any, predicate: Any) -> Any | None:
    for frame in page.frames:
        try:
            if predicate(frame):
                return frame
        except Exception:
            continue
    return None


def _find_frame_by_url_contains(page: Any, token: str) -> Any | None:
    return _find_frame(page, lambda frame: token in (frame.url or ""))


def _find_frame_by_text(page: Any, text: str) -> Any | None:
    return _find_frame(page, lambda frame: text in _safe_body_text(frame))


def _click(locator: Any, *, timeout: int = 8000) -> bool:
    try:
        locator.click(timeout=timeout)
        return True
    except Exception:
        return False


def _check(locator: Any, *, timeout: int = 3000) -> bool:
    try:
        locator.check(timeout=timeout)
        return True
    except Exception:
        return False


def _dismiss_dialog(page: Any) -> None:
    dialog = _find_frame_by_url_contains(page, "epaas-dialog-frame")
    if not dialog:
        return
    for text in ("关闭", "知道了", "我知道了"):
        if _click(dialog.get_by_text(text, exact=True).first, timeout=1500):
            page.wait_for_timeout(800)
            return


def _load_profile_or_fallback() -> dict[str, Any]:
    try:
        return load_jst_browser_profile(DEFAULT_SCENE)
    except FileNotFoundError:
        return {
            "site": "jst_erp",
            "scene": DEFAULT_SCENE,
            "page_url": JST_FALLBACK_URL,
            "page_title": "聚水潭ERP",
            "source": "fallback_without_learn",
        }


def _open_root_page(page: Any, profile: dict[str, Any]) -> None:
    target_url = profile.get("page_url") or JST_FALLBACK_URL
    page.goto(str(target_url), wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)


def _ensure_login(page: Any) -> None:
    # 登录失效时把专用 9222 Chrome 切到前台让用户手动登录（唯一允许的弹窗场景）。
    surface_jst_login_if_needed(page)


def _open_old_shop_goods_page(page: Any) -> Any:
    old_frame = _find_frame_by_url_contains(page, "itemContrast.aspx")
    if old_frame:
        return old_frame

    new_frame = _find_frame(page, lambda frame: "旧版店铺商品管理" in _safe_body_text(frame))
    if not new_frame:
        if not _click(page.get_by_text("商品", exact=True).first):
            raise RuntimeError("找不到菜单：商品")
        page.wait_for_timeout(1000)
        if not _click(page.get_by_text("店铺商品管理", exact=True).first):
            home_frame = _find_frame_by_text(page, "店铺商品管理")
            if not home_frame or not _click(home_frame.get_by_text("店铺商品管理", exact=True).first):
                raise RuntimeError("找不到入口：店铺商品管理")
        page.wait_for_timeout(4000)
        new_frame = _find_frame(page, lambda frame: "旧版店铺商品管理" in _safe_body_text(frame))

    if not new_frame:
        raise RuntimeError("未进入店铺商品管理页面。")

    if not _click(new_frame.get_by_text("旧版店铺商品管理", exact=True).first):
        raise RuntimeError("找不到入口：旧版店铺商品管理")
    page.wait_for_timeout(1500)

    dialog = _find_frame_by_url_contains(page, "epaas-dialog-frame")
    if dialog:
        for text in ("继续返回旧版", "返回旧版", "确定"):
            if _click(dialog.get_by_text(text, exact=True).first, timeout=3000):
                page.wait_for_timeout(3500)
                break

    old_frame = _find_frame_by_url_contains(page, "itemContrast.aspx")
    if not old_frame:
        raise RuntimeError("未成功进入旧版店铺商品管理。")
    return old_frame


def _open_import_frame(page: Any) -> Any:
    existing = _find_frame_by_url_contains(page, "importshopitemsku.aspx")
    if existing:
        return existing

    old_frame = _open_old_shop_goods_page(page)
    clicked = _click(old_frame.get_by_text("导入店铺商品资料", exact=True).first)
    if not clicked:
        if not _click(old_frame.get_by_text("导入", exact=True).first):
            raise RuntimeError("找不到入口：导入")
        page.wait_for_timeout(800)
        if not _click(old_frame.get_by_text("导入店铺商品资料", exact=True).first):
            raise RuntimeError("找不到入口：导入店铺商品资料")
    page.wait_for_timeout(2500)

    import_frame = _find_frame_by_url_contains(page, "importshopitemsku.aspx")
    if not import_frame:
        raise RuntimeError("未打开导入店铺商品资料窗口。")
    return import_frame


def _select_shop(import_frame: Any, shop_name: str) -> dict[str, str]:
    import_frame.locator("#shop_id_txt").click(timeout=5000)
    import_frame.get_by_text(shop_name, exact=True).click(timeout=5000)
    shop_id = import_frame.locator("#shop_id").input_value(timeout=2000)
    selected_text = import_frame.locator("#shop_id_txt").input_value(timeout=2000)
    if shop_name not in selected_text:
        raise RuntimeError(f"店铺选择失败：{shop_name}")
    return {"shop_id": shop_id, "shop_name": selected_text}


def _select_mode(import_frame: Any, mode: str) -> None:
    normalized = str(mode or "").strip().lower()
    canonical_mode = next((name for name, aliases in MODE_ALIASES.items() if normalized in aliases), None)
    if canonical_mode is None:
        raise RuntimeError("当前仅支持 --mode ignore 或 --mode cover。")

    selector_candidates = {
        "ignore": ["#ignore", 'input[type="radio"][value="ignore"]'],
        "cover": ["#cover", 'input[type="radio"][value="cover"]', 'input[type="radio"][value="overwrite"]'],
    }
    label_candidates = {
        "ignore": ["忽略", "ignore"],
        "cover": ["覆盖", "cover", "overwrite"],
    }

    for selector in selector_candidates[canonical_mode]:
        if _check(import_frame.locator(selector).first):
            return
    for label in label_candidates[canonical_mode]:
        if _click(import_frame.get_by_text(label, exact=True).first, timeout=2000):
            return

    raise RuntimeError(f"导入模式选择失败：{canonical_mode}")


def _upload_excel(import_frame: Any, excel_path: Path) -> None:
    import_frame.locator("#FileUpload1").set_input_files(str(excel_path), timeout=5000)


def _submit_and_collect(page: Any, import_frame: Any) -> dict[str, Any]:
    import_frame.locator("#Button1").click(timeout=5000)
    page.wait_for_timeout(8000)

    dialog = _find_frame_by_url_contains(page, "epaas-dialog-frame")
    dialog_text = _safe_body_text(dialog) if dialog else ""
    if not dialog_text:
        raise RuntimeError("提交后未看到导入结果提示。")

    match = re.search(r"总共有(\d+)条导入成功,有(\d+)条导入失败", dialog_text)
    success_count = int(match.group(1)) if match else None
    failed_count = int(match.group(2)) if match else None
    return {
        "dialog_text": dialog_text,
        "success_count": success_count,
        "failed_count": failed_count,
    }


def import_jst_shop_goods(*, file_path: str | Path, shop_name: str, mode: str = "ignore") -> CommandResponse:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore

    excel_path = Path(file_path).expanduser()
    if not excel_path.exists():
        raise FileNotFoundError(f"导入 Excel 不存在：{excel_path}")

    get_scene_manager().ensure_scene(JST_SITE, JST_AUTH_SCENE)
    profile = _load_profile_or_fallback()

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(SESSIONHUB_CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"9222 浏览器未启动。请先执行：\n{SESSIONHUB_CHROME_COMMAND}\n原始错误：{exc}") from exc

        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        try:
            _open_root_page(page, profile)
            _ensure_login(page)
            import_frame = _open_import_frame(page)
            selected_shop = _select_shop(import_frame, shop_name)
            _select_mode(import_frame, mode)
            _upload_excel(import_frame, excel_path)
            result = _submit_and_collect(page, import_frame)
            screenshot_path = _screenshot(page, "jst_shop_goods_import_result")
            _dismiss_dialog(page)
        except Exception as exc:
            screenshot_path = _screenshot(page, "jst_shop_goods_import_failed")
            raise RuntimeError(f"聚水潭店铺商品导入失败，已保存截图：{screenshot_path}。原因：{exc}") from exc

    return CommandResponse(
        success=True,
        platform="jst",
        command="shop-goods import",
        data={
            "file": str(excel_path),
            "shop_name": selected_shop["shop_name"],
            "shop_id": selected_shop["shop_id"],
            "mode": mode,
            "status": "submitted",
            "scene": profile.get("scene", DEFAULT_SCENE),
            "scene_source": profile.get("source", "unknown"),
            "success_count": result.get("success_count"),
            "failed_count": result.get("failed_count"),
            "result_text": result.get("dialog_text"),
            "screenshot_path": screenshot_path,
        },
    )
