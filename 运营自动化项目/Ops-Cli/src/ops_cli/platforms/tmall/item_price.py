"""天猫公开商品页实时价格读取（控价监控用）。

读取路径（真实模式）：纯商品ID先用猫超商品列表 searchItem 查询 ``mid`` 并补成
带 ``mi_id`` 的完整详情链接；无完整链接时再用 SessionHub 9222 + Playwright 打开
H5 详情页，捕获 ``mtop.taobao.detail.data.get`` 结构化到手价；失败时回退 PC 详情页
DOM 主价提取 + 页面文本正则兜底，并对每个商品截图存证。

本层只负责「读取原始数值」并输出统一 JSON（每个商品一行）：
- item_id / title / realtime_price / raw_price_text / screenshot_path / captured_at / capture_status

控价对比、差价、状态判定全部交给业务层 workflow，本层不做任何对比，也不读取控价。

capture_status 取值（业务层据此映射最终状态）：
- ``ok``            正常读到价格
- ``price_context_missing``只给商品ID时检测到天猫超市裸价或活动信号，价格可能缺少 mi_id 上下文
- ``price_empty``   页面打开但价格元素/文本都解析不到
- ``item_not_found``商品不存在 / 已下架
- ``login_required``跳转到登录页（已 bring_to_front 提示登录）
- ``captcha``       命中滑块 / 安全验证（直接退出，绝不死循环重试）
- ``failed``        其它异常（单个商品失败不影响其它）

dry-run 返回 ``simulated=True`` 的占位数据 + 占位截图，不访问任何页面。
"""

from __future__ import annotations

import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.runtime_context import write_runtime_context


TMALL_ITEM_URL = "https://detail.tmall.com/item.htm?id={item_id}"
TMALL_H5_ITEM_URL = "https://h5.m.taobao.com/awp/core/detail.htm?id={item_id}"
MTOP_DETAIL_API_HINT = "mtop.taobao.detail.data.get"

# 1x1 透明 PNG，dry-run 占位截图用（不访问页面也能产出 screenshot_path）。
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)

# 价格元素多级选择器兜底（天猫新旧详情页 + 淘宝详情页都尽量覆盖）。
# 真实 DOM 会随页面改版变化，命中失败时再退化到页面文本正则。
PRICE_SELECTORS: tuple[str, ...] = (
    '[class*="Price--priceText"]',
    '[class*="priceText"]',
    '.tm-promo-price .tm-price',
    '#J_PromoPrice .tm-price',
    '#J_StrPrice .tm-price-cur',
    '.tm-price',
    'strong.tb-rmb-num',
    '.tb-rmb-num',
    '#J_PromoPriceNum',
    '[class*="originPrice"] [class*="text"]',
)

# 主价「价格块」提取 JS（抗混淆类名、抗推荐位/优惠券干扰）。
# 天猫超市/天猫详情页价格元素 class 是构建期哈希（如 text--Do8Zgb3q），无法硬编码选择器；
# 且价格块结构不一：有的是干净的「￥588.81」叶子，有的是「超市推荐￥624起直降126元」整块。
# 统一策略：取「字号最大、含 ¥金额、文本不太长（≤30 字）」的可见块文本，回传给纯函数解析，
# 由 parse_deal_price_from_block 算「参考价 − 直降/立减」得到到手价（主价字号大~28px，
# 推荐位/划线价字号小，永远选到主价）。
PRICE_BLOCK_JS = r"""
() => {
  let best = null;
  for (const el of document.querySelectorAll('body *')) {
    if (el.children.length > 4) continue;
    const t = (el.textContent || '').replace(/\s+/g, '');
    if (!t || t.length > 30) continue;
    if (!/[¥￥]\d/.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.top < 0) continue;   // 不可见/视口外跳过
    let fs = parseFloat(getComputedStyle(el).fontSize) || 0;      // 价格数字的最大视觉字号
    for (const c of el.querySelectorAll('*')) {
      const f = parseFloat(getComputedStyle(c).fontSize) || 0;
      if (f > fs) fs = f;
    }
    if (!best || fs > best.fs || (fs === best.fs && r.top < best.top)) best = { t, fs, top: r.top };
  }
  return best ? best.t : null;
}
"""

# 标题选择器兜底。
TITLE_SELECTORS: tuple[str, ...] = (
    '[class*="ItemTitle--mainTitle"]',
    '[class*="mainTitle"]',
    '.tb-detail-hd h1',
    'h1[data-title]',
    'h1',
)

# 商品不存在 / 已下架 文案。
_NOT_FOUND_HINTS: tuple[str, ...] = (
    "很抱歉，您查看的商品找不到了",
    "您查看的商品找不到了",
    "该商品已下架",
    "商品已下架",
    "宝贝已下架",
    "商品不存在",
    "页面找不到了",
)

# 滑块 / 安全验证 文案（命中即退出，不重试）。
_CAPTCHA_HINTS: tuple[str, ...] = (
    "滑动验证",
    "滑块",
    "安全验证",
    "请输入验证码",
    "向右滑动",
    "拖动下方滑块",
)
_CAPTCHA_URL_HINTS: tuple[str, ...] = ("captcha", "punish", "_____tmd_____", "nocaptcha")
_PRICE_PATH_BLOCKLIST: tuple[str, ...] = (
    "address",
    "coupon",
    "couponprice",
    "delivery",
    "freight",
    "installment",
    "service",
    "tax",
)
_PRICE_KEY_HINTS: tuple[str, ...] = (
    "actualprice",
    "finalprice",
    "handprice",
    "price",
    "pricetext",
    "promotionprice",
    "saleprice",
    "soldprice",
    "到手",
    "券后",
    "价格",
)
_DEAL_PRICE_LABELS: tuple[tuple[str, int], ...] = (
    ("领消费券后的", 140),
    ("领消费券后", 140),
    ("领券后的", 135),
    ("领券后", 135),
    ("券后", 130),
    ("平台加补后", 120),
    ("加补后", 115),
    ("补贴后", 110),
    ("到手价", 105),
    ("到手", 100),
)
_PROMO_CONTEXT_HINTS: tuple[str, ...] = (
    "领取政府补贴",
    "政府补贴",
    "超市补贴",
    "官方立减",
    "平台加补",
    "加补",
    "补贴",
    "领消费券",
    "消费券",
    "领券",
    "券后",
    "立即领取",
)
_PRICE_CONTEXT_MISSING_ERROR = (
    "检测到天猫超市裸价或补贴/领券活动信号，但未提供完整商品链接，"
    "可能缺少 mi_id 活动上下文；请复制完整链接重跑。"
)


def parse_money(text: str | None) -> float | None:
    """从一段文本里解析第一个金额数字，解析不到返回 None（纯函数，便于单测）。"""
    if not text:
        return None
    cleaned = text.replace(",", "").replace("，", "")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not match:
        return None
    try:
        return round(float(match.group(0)), 2)
    except ValueError:
        return None


def parse_price_from_text(text: str | None) -> tuple[float | None, str]:
    """页面文本兜底：在「¥/￥ + 数字」里取第一个作为当前价。

    返回 (price, raw_text)。解析不到返回 (None, "")。商品详情页一般首个出现的
    ¥ 金额即当前展示价；更精细的促销/到手价解析留待 scene 调优。
    """
    if not text:
        return None, ""
    match = re.search(r"[¥￥]\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)", text)
    if not match:
        return None, ""
    raw = match.group(0)
    return parse_money(match.group(1)), raw


def parse_labeled_deal_price_from_text(text: str | None) -> tuple[float | None, str]:
    """从页面文本里优先解析带“领券后/加补后”等标签的实际到手价。"""
    if not text:
        return None, ""
    candidates: list[tuple[int, int, float, str]] = []
    for label, score in _DEAL_PRICE_LABELS:
        # 收紧：标签与金额之间最多 10 个「非货币符号、非数字」字符，且金额必须紧跟 ¥/￥。
        # 这样「券后返5元后到手 ¥380」不会误抓 5（5 是数字，会截断 gap），只认带 ¥ 的真实价格。
        pattern = rf"{re.escape(label)}[^¥￥\d]{{0,10}}[¥￥]\s*(\d{{1,3}}(?:,\d{{3}})*(?:\.\d+)?|\d+(?:\.\d+)?)"
        for match in re.finditer(pattern, text, re.S):
            price = parse_money(match.group(1))
            if price is None:
                continue
            raw = re.sub(r"\s+", " ", match.group(0)).strip()
            candidates.append((score, match.start(), price, raw))
    if not candidates:
        return None, ""
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _, _, price, raw = candidates[0]
    return price, raw


def parse_deal_price_from_block(text: str | None) -> tuple[float | None, str]:
    """从「主价块」文本算到手价：参考价 − 直降/立减（纯函数，便于单测）。

    例：
    - "活动价￥588.81"            -> 588.81（无直降）
    - "超市推荐￥624起直降126元"  -> 624 - 126 = 498
    - "到手价￥498"               -> 498
    解析不到参考价返回 (None, "")；直降异常（减成 ≤0）时退回参考价，避免算出负数。
    """
    if not text:
        return None, ""
    match = re.search(r"[¥￥]\s*(\d[\d,]*(?:\.\d+)?)", text)
    if not match:
        return None, ""
    reference = parse_money(match.group(1))
    if reference is None:
        return None, ""
    cut = 0.0
    for cm in re.finditer(r"(?:直降|立减)\s*(\d+(?:\.\d+)?)\s*元?", text):
        try:
            cut += float(cm.group(1))
        except ValueError:
            continue
    if cut <= 0:
        return reference, text
    deal = round(reference - cut, 2)
    if deal <= 0:  # 直降金额异常（可能对应划线原价），放弃减法用参考价兜底。
        return reference, text
    return deal, text


def needs_full_url_context(
    text: str | None,
    raw_price_text: str | None,
    source_url: str | None = "",
    *,
    page_url: str | None = "",
) -> bool:
    """纯商品ID抓到裸价且页面有活动信号时，认为缺少 mi_id 等上下文。"""
    if source_url:
        return False
    if not text or not raw_price_text:
        return False
    if parse_money(raw_price_text) is None:
        return False
    if any(label in raw_price_text for label, _ in _DEAL_PRICE_LABELS):
        return False
    if "chaoshi.detail.tmall.com" in (page_url or "").lower():
        return True
    return any(hint in text for hint in _PROMO_CONTEXT_HINTS)


def _parse_mtop_response_text(text: str | None) -> dict[str, Any] | None:
    """解析 mtop JSON / JSONP 响应文本，失败返回 None。"""
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    try:
        if cleaned.startswith("{"):
            data = json.loads(cleaned)
        else:
            match = re.search(r"^[^(]*\((.*)\)\s*;?$", cleaned, re.S)
            if not match:
                return None
            data = json.loads(match.group(1))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _parse_embedded_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    if not cleaned or cleaned[0] not in "{[":
        return value
    try:
        return json.loads(cleaned)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value


def _price_path_score(path: str) -> int:
    lowered = path.lower()
    if any(blocked in lowered for blocked in _PRICE_PATH_BLOCKLIST):
        return -1

    score = -1
    if "soldpricetext" in lowered or "finalprice" in lowered or "actualprice" in lowered or "handprice" in lowered:
        score = max(score, 130)
    if "到手" in path or "券后" in path:
        score = max(score, 125)
    if ".price.price.pricetext" in lowered:
        score = max(score, 120)
    if "pricemodule" in lowered and "price" in lowered:
        score = max(score, 110)
    if "skucore.sku2info" in lowered and "price" in lowered:
        score = max(score, 90)
    if lowered.endswith("pricetext") or lowered.endswith("price.price"):
        score = max(score, 70)
    if any(hint in lowered for hint in _PRICE_KEY_HINTS):
        score = max(score, 50)
    return score


def _iter_mtop_price_candidates(value: Any, path: str = ""):
    parsed = _parse_embedded_json(value)
    if parsed is not value:
        yield from _iter_mtop_price_candidates(parsed, path)
        return

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _iter_mtop_price_candidates(child, child_path)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield from _iter_mtop_price_candidates(child, child_path)
        return

    score = _price_path_score(path)
    if score < 0:
        return
    if isinstance(value, (int, float)):
        raw = str(value)
    elif isinstance(value, str):
        raw = value.strip()
    else:
        return
    price = parse_money(raw)
    if price is None:
        return
    yield score, path, price, raw


def extract_price_from_mtop_payload(payload: dict[str, Any] | None) -> tuple[float | None, str]:
    """从 mtop.taobao.detail.data.get 响应提取到手价。

    已知结构优先级：
    - data.apiStack[].value(JSON).price.price.priceText / priceModule.*PriceText
    - data.apiStack[].value(JSON).skuCore.sku2info.*.price.priceText
    - 其它显式 sold/final/actual/hand/priceText 字段

    当前 9222 探测到 H5 在部分登录态下可能返回 redirectToV3 或 RGV587 风控响应；
    这些结构不会产出价格，调用方继续回退 DOM/文本兜底。
    """
    if not isinstance(payload, dict):
        return None, ""
    candidates = list(_iter_mtop_price_candidates(payload))
    if not candidates:
        return None, ""
    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    _, _, price, raw = candidates[0]
    return price, raw


def classify_page(url: str, text: str) -> str | None:
    """根据 url / 页面文本判定异常态；正常返回 None。"""
    lowered_url = (url or "").lower()
    parsed = urlparse(lowered_url)
    if "login" in lowered_url or (parsed.path or "").startswith("/member/login"):
        return "login_required"
    if any(hint in lowered_url for hint in _CAPTCHA_URL_HINTS):
        return "captcha"
    blob = text or ""
    if any(hint in blob for hint in _CAPTCHA_HINTS):
        return "captcha"
    if any(hint in blob for hint in _NOT_FOUND_HINTS):
        return "item_not_found"
    return None


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _item_id_from_ref(ref: str) -> str:
    text = (ref or "").strip()
    match = re.search(r"[?&]id=(\d+)", text)
    if match:
        return match.group(1)
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def parse_item_refs(item_ids: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for piece in (item_ids or "").replace("，", ",").split(","):
        raw = piece.strip()
        if not raw:
            continue
        item_id = _item_id_from_ref(raw)
        refs.append({"item_id": item_id, "source_url": raw if raw.lower().startswith(("http://", "https://")) else ""})
    return refs


def _query_tmall_activity_urls(item_ids: list[str]) -> dict[str, str]:
    from ops_cli.platforms.tmcs.product import query_tmall_activity_urls

    return query_tmall_activity_urls(item_ids)


def complete_item_refs_with_activity_urls(item_refs: list[dict[str, str]]) -> list[dict[str, str]]:
    """纯商品ID先从猫超商品列表 searchItem 查询 mid，补全带 mi_id 的详情链接。"""
    completed = [dict(ref) for ref in item_refs]
    missing_ids = [ref["item_id"] for ref in completed if ref.get("item_id") and not ref.get("source_url")]
    if not missing_ids:
        return completed
    try:
        activity_urls = _query_tmall_activity_urls(missing_ids)
    except Exception:
        return completed
    for ref in completed:
        if not ref.get("source_url"):
            ref["source_url"] = activity_urls.get(ref.get("item_id", ""), "")
    return completed


def _ensure_dir(path: str | Path) -> Path:
    directory = Path(path).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _simulated_row(item_id: str, screenshot_dir: Path) -> dict[str, Any]:
    shot = screenshot_dir / f"tmall_item_{item_id}_{_timestamp()}.png"
    shot.write_bytes(_PNG_BYTES)
    return {
        "item_id": item_id,
        "title": f"【模拟】天猫商品 {item_id}",
        "realtime_price": 1299.00,
        "raw_price_text": "¥1299.00",
        "screenshot_path": str(shot),
        "captured_at": _now_iso(),
        "capture_status": "ok",
        "error": None,
    }


def _collect_page_text(page: Any) -> str:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    parts: list[str] = []
    for frame in page.frames:
        try:
            parts.append(frame.locator("body").inner_text(timeout=2000))
        except PlaywrightError:
            continue
    return "\n".join(parts)


def _first_selector_text(page: Any, selectors: tuple[str, ...]) -> str:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    for frame in page.frames:
        for selector in selectors:
            try:
                locator = frame.locator(selector).first
                if locator.count() == 0:
                    continue
                value = (locator.inner_text(timeout=1500) or "").strip()
                if value:
                    return value
            except PlaywrightError:
                continue
    return ""


def extract_title(page: Any) -> str:
    title = _first_selector_text(page, TITLE_SELECTORS)
    if title:
        return title
    try:
        raw = (page.title() or "").strip()
    except Exception:
        raw = ""
    # 去掉「-天猫Tmall.com」「-淘宝网」等后缀。
    for suffix in ("-天猫Tmall.com", "-tmall.com天猫", "-淘宝网", "-Taobao", "_天猫", "_淘宝"):
        idx = raw.find(suffix)
        if idx > 0:
            return raw[:idx].strip()
    return raw


def _extract_price_via_dom(page: Any) -> tuple[float | None, str]:
    """逐帧取最显眼的主价块，按「参考价 − 直降/立减」算到手价。返回 (price, raw)。"""
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    for frame in page.frames:
        try:
            block = frame.evaluate(PRICE_BLOCK_JS)
        except PlaywrightError:
            continue
        if block:
            price, raw = parse_deal_price_from_block(str(block))
            if price is not None:
                return price, raw
    return None, ""


def extract_price(page: Any) -> tuple[float | None, str]:
    """四级提取，返回 (price, raw_text)：

    1) DOM 最显眼主价块算到手价（参考价 − 直降/立减；抗混淆类名、抗推荐位/划线价）；
    2) 页面文本里带「领券后/加补后/到手价」标签的实际到手价（块法拿不到时兜底）；
    3) 老选择器兜底（旧版天猫详情页）；
    4) 页面文本「第一个 ¥金额」兜底（最不稳，仅前三级都拿不到时用）。
    """
    price, raw = _extract_price_via_dom(page)
    if price is not None:
        return price, raw

    price, raw = parse_labeled_deal_price_from_text(_collect_page_text(page))
    if price is not None:
        return price, raw

    raw = _first_selector_text(page, PRICE_SELECTORS)
    price = parse_money(raw)
    if price is not None:
        return price, raw

    return parse_price_from_text(_collect_page_text(page))


def _read_price_via_mtop(page: Any, item_id: str) -> tuple[float | None, str]:
    """打开 H5 详情页并捕获 detail.data.get 响应，成功返回结构化到手价。

    用 Playwright 自带 page.on("response") 收集匹配响应，导航结束后再读 body，
    避免在事件回调里 reentrant 调用 CDP，也不残留 CDP 会话/监听（批量复用同一 page）。
    """
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    responses: list[Any] = []

    def on_response(response: Any) -> None:
        try:
            if MTOP_DETAIL_API_HINT in (response.url or ""):
                responses.append(response)
        except Exception:
            pass

    page.on("response", on_response)
    try:
        page.goto(TMALL_H5_ITEM_URL.format(item_id=item_id), wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
    except PlaywrightError:
        return None, ""
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

    for response in responses:
        try:
            payload = _parse_mtop_response_text(response.text())
        except Exception:
            continue
        price, raw = extract_price_from_mtop_payload(payload)
        if price is not None:
            return price, raw
    return None, ""


def _wait_for_price(page: Any, *, deadline_ms: int = 12000, step_ms: int = 1000) -> tuple[float | None, str]:
    """轮询直到价格元素渲染出来（异步价格 + 避免抓到半加载的瞬时错误值）。"""
    waited = 0
    last = (None, "")
    while True:
        last = extract_price(page)
        if last[0] is not None:
            return last
        if waited >= deadline_ms:
            return last
        page.wait_for_timeout(step_ms)
        waited += step_ms


def _save_screenshot(page: Any, path: Path) -> str:
    """尽力截图存证；失败不影响价格读取，返回空字符串。"""
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 9222 Chrome 在后台被遮挡时合成器不产帧，激活标签页恢复产帧（不抢 OS 焦点）。
        page.bring_to_front()
        page.wait_for_timeout(300)
        page.screenshot(path=str(path), full_page=False, timeout=10000, animations="allow")
        return str(path)
    except (PlaywrightError, Exception):  # noqa: BLE001 - 截图失败可降级
        return ""


def _is_current_detail_page(page: Any, item_id: str) -> bool:
    url = str(getattr(page, "url", "") or "").lower()
    return item_id in url and ("detail.tmall.com" in url or "detail.taobao.com" in url)


def _extract_one(page, item_id: str, screenshot_dir: Path, *, source_url: str = "") -> dict[str, Any]:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore

    shot_path = screenshot_dir / f"tmall_item_{item_id}_{_timestamp()}.png"
    row: dict[str, Any] = {
        "item_id": item_id,
        "title": "",
        "realtime_price": None,
        "raw_price_text": "",
        "screenshot_path": "",
        "captured_at": _now_iso(),
        "capture_status": "failed",
        "error": None,
    }

    if not source_url:
        # 优先走 H5 mtop 结构化响应。阶段0实测：桌面 UA 可能只返回 redirectToV3，
        # 移动 UA 在当前 9222 登录态下可能触发 RGV587；因此解析失败继续走 DOM 兜底。
        price, raw = _read_price_via_mtop(page, item_id)

        page.wait_for_timeout(500)
        text = _collect_page_text(page)
        status = classify_page(page.url, text)

        if status == "login_required":
            row["screenshot_path"] = _save_screenshot(page, shot_path)
            _bring_chrome_to_front_safe()
            row["capture_status"] = "login_required"
            row["error"] = "检测到登录页，请在主浏览器完成登录后重试。"
            return row
        if status == "captcha":
            row["screenshot_path"] = _save_screenshot(page, shot_path)
            _bring_chrome_to_front_safe()
            row["capture_status"] = "captcha"
            row["error"] = "命中滑块/安全验证，已退出，请人工处理后重试（不自动重试）。"
            return row
        if status == "item_not_found":
            row["screenshot_path"] = _save_screenshot(page, shot_path)
            row["capture_status"] = "item_not_found"
            row["error"] = "商品不存在或已下架。"
            return row

        if price is not None:
            if needs_full_url_context(text, raw, source_url, page_url=page.url):
                row["title"] = extract_title(page)
                row["screenshot_path"] = _save_screenshot(page, shot_path)
                row["raw_price_text"] = raw
                row["capture_status"] = "price_context_missing"
                row["error"] = _PRICE_CONTEXT_MISSING_ERROR
                return row
            row["title"] = extract_title(page)
            row["screenshot_path"] = _save_screenshot(page, shot_path)
            row["realtime_price"] = price
            row["raw_price_text"] = raw
            row["capture_status"] = "ok"
            return row

    if source_url or not _is_current_detail_page(page, item_id):
        try:
            page.goto(source_url or TMALL_ITEM_URL.format(item_id=item_id), wait_until="domcontentloaded", timeout=30000)
        except PlaywrightError as exc:
            row["error"] = f"打开商品页失败：{exc}"
            return row

    # 先给最少渲染时间，判断登录/滑块/下架等异常态。
    page.wait_for_timeout(1500)
    text = _collect_page_text(page)
    status = classify_page(page.url, text)

    if status == "login_required":
        row["screenshot_path"] = _save_screenshot(page, shot_path)
        _bring_chrome_to_front_safe()
        row["capture_status"] = "login_required"
        row["error"] = "检测到登录页，请在主浏览器完成登录后重试。"
        return row
    if status == "captcha":
        row["screenshot_path"] = _save_screenshot(page, shot_path)
        _bring_chrome_to_front_safe()
        row["capture_status"] = "captcha"
        row["error"] = "命中滑块/安全验证，已退出，请人工处理后重试（不自动重试）。"
        return row
    if status == "item_not_found":
        row["screenshot_path"] = _save_screenshot(page, shot_path)
        row["capture_status"] = "item_not_found"
        row["error"] = "商品不存在或已下架。"
        return row

    # 价格异步渲染：轮询到出价为止，避免抓到半加载的瞬时错误值。
    price, raw = _wait_for_price(page)
    text = _collect_page_text(page)
    row["title"] = extract_title(page)
    row["screenshot_path"] = _save_screenshot(page, shot_path)  # 出价后再截图，存证与读数一致
    if price is not None and needs_full_url_context(text, raw, source_url, page_url=page.url):
        row["raw_price_text"] = raw
        row["capture_status"] = "price_context_missing"
        row["error"] = _PRICE_CONTEXT_MISSING_ERROR
        return row
    row["realtime_price"] = price
    row["raw_price_text"] = raw
    row["capture_status"] = "ok" if price is not None else "price_empty"
    if price is None:
        row["error"] = "未能从页面解析到价格。"
    return row


def _bring_chrome_to_front_safe() -> None:
    try:
        from scene.chrome_cdp import bring_chrome_to_front  # type: ignore

        bring_chrome_to_front()
    except Exception:
        pass


def _read_item_prices(item_refs: list[dict[str, str]], screenshot_dir: Path) -> list[dict[str, Any]]:
    """单次 CDP 连接，复用一个标签页依次读取所有商品价格。"""
    root = _sessionhub_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from scene.chrome_cdp import CDP_URL, start_chrome  # type: ignore
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

    item_refs = complete_item_refs_with_activity_urls(item_refs)
    rows: list[dict[str, Any]] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        created_page = len(context.pages) == 1 and page.url == "about:blank"
        try:
            for index, item_ref in enumerate(item_refs):
                item_id = item_ref["item_id"]
                # 单个商品失败不影响其它商品（批量稳健性）。
                try:
                    rows.append(_extract_one(page, item_id, screenshot_dir, source_url=item_ref.get("source_url", "")))
                except Exception as exc:  # noqa: BLE001
                    rows.append(
                        {
                            "item_id": item_id,
                            "title": "",
                            "realtime_price": None,
                            "raw_price_text": "",
                            "screenshot_path": "",
                            "captured_at": _now_iso(),
                            "capture_status": "failed",
                            "error": f"读取异常：{exc}",
                        }
                    )
                if index < len(item_refs) - 1:
                    page.wait_for_timeout(random.randint(800, 2000))
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass
    return rows


def run_item_price(*, item_ids: str, screenshot_dir: str | Path, dry_run: bool = False) -> CommandResponse:
    item_refs = parse_item_refs(item_ids)
    if not item_refs:
        raise RuntimeError("缺少 --item-ids（逗号分隔的天猫商品ID）。")

    out_dir = _ensure_dir(screenshot_dir)
    ids = [ref["item_id"] for ref in item_refs]
    inputs = {"item_ids": ids, "screenshot_dir": str(out_dir), "dry_run": dry_run}

    if dry_run:
        rows = [_simulated_row(item_id, out_dir) for item_id in ids]
        source = "simulated"
    else:
        rows = _read_item_prices(item_refs, out_dir)
        source = "page"

    artifacts = [row["screenshot_path"] for row in rows if row.get("screenshot_path")]
    context_path = write_runtime_context(
        task_name="tmall_item_price",
        status="success",
        inputs=inputs,
        outputs={"rows": rows, "source": source},
    )
    return CommandResponse(
        success=True,
        platform="tmall",
        command="price get",
        data={
            "rows": rows,
            "count": len(rows),
            "source": source,
            "simulated": dry_run,
            "dry_run": dry_run,
            "artifacts": artifacts,
            "context_path": str(context_path),
        },
    )
