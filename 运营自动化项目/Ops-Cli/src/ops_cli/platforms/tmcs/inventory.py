from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from ops_cli.browser import managed_work_page
from ops_cli.capabilities import recovery_must_fail_fast
from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.platforms.tmcs.shared import TMCS_INVENTORY_EXPORT_FILENAME
from ops_cli.platforms.tmcs.shared import TMCS_INVENTORY_EXPORT_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_INVENTORY_ADJUST_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_INVENTORY_SEARCH_SCENE
from ops_cli.platforms.tmcs.shared import TMCS_SITE
from ops_cli.platforms.tmcs.shared import check_scene_or_fail
from ops_cli.platforms.tmcs.shared import extract_export_task_id
from ops_cli.platforms.tmcs.shared import filter_cookies_for_url
from ops_cli.platforms.tmcs.shared import find_download_url
from ops_cli.platforms.tmcs.shared import form_encode
from ops_cli.platforms.tmcs.shared import gei_task_download_url
from ops_cli.platforms.tmcs.shared import is_probably_excel
from ops_cli.platforms.tmcs.shared import merge_cookie_header
from ops_cli.platforms.tmcs.shared import read_json
from ops_cli.platforms.tmcs.shared import resolve_download_content
from ops_cli.platforms.tmcs.shared import sanitize_replay_headers
from ops_cli.platforms.tmcs.shared import scene_store_path
from ops_cli.platforms.tmcs.shared import sessionhub_root
from ops_cli.platforms.tmcs.shared import tmcs_download
from ops_cli.platforms.tmcs.shared import tmcs_request
from ops_cli.platforms.tmcs.shared import trim_cookie_header
from ops_cli.platforms.tmcs.shared import unique_path
from ops_cli.platforms.tmcs.shared import write_json
from ops_cli.runtime_context import write_runtime_context


TEMPLATE_PATH = Path("data/tmcs/inventory_export_template.json")
ADJUST_TEMPLATE_PATH = Path("data/tmcs/inventory_adjust_template.json")
PRIMARY_PROBE_PATH = Path("runtime/context/tmcs_inventory_primary_probe_latest.json")
DEFAULT_WAREHOUSE_CODE = "mc_aokesi_suolong"
INVENTORY_PAGE_URL = "https://web.txcs.tmall.com/pages/chaoshi/indus_half_tube_inventory_management_qtg?_c_lang=zh-cn&iframeContainerFrom=tm&__IFRAME_CONTAINER_IFRAME_ID__=2"
SEARCH_URL_KEYWORD = "aic.cbbs.tmall.com/one-stock/listOneStockAllTubeInventory"
EXPORT_URL_KEYWORD = "tools.cbbs.tmall.com/gei/export/task/"
QUERY_SELLABLE_URL = "https://aic.cbbs.tmall.com/one-stock/all-tube/adjust-order/querySellableQuantity"
INCREASE_ADJUST_URL = "https://aic.cbbs.tmall.com/one-stock/all-tube/adjust-order/createIncreaseAdjustOrder"
DECREASE_ADJUST_URL = "https://aic.cbbs.tmall.com/one-stock/all-tube/adjust-order/createDecreaseAdjustOrder"


def _template_path() -> Path:
    return Path.cwd() / TEMPLATE_PATH


def _adjust_template_path() -> Path:
    return Path.cwd() / ADJUST_TEMPLATE_PATH


def _primary_probe_path() -> Path:
    return Path.cwd() / PRIMARY_PROBE_PATH


def _scene_path(scene: str) -> Path:
    return scene_store_path(TMCS_SITE, scene)


def _sessionhub_root() -> Path:
    return sessionhub_root()


def _write_template(*, search_scene: dict[str, Any], export_scene: dict[str, Any]) -> Path:
    template = {
        "site": TMCS_SITE,
        "scenes": {
            "inventory_search": TMCS_INVENTORY_SEARCH_SCENE,
            "inventory_export": TMCS_INVENTORY_EXPORT_SCENE,
        },
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "defaults": {
            "output_dir": get_config().tmcs_bill_download_dir,
            "warehouse_code": DEFAULT_WAREHOUSE_CODE,
            "file_name": TMCS_INVENTORY_EXPORT_FILENAME,
        },
        "inventory_search": {
            "url": search_scene.get("url"),
            "method": search_scene.get("method"),
            "headers": _sanitize_inventory_headers(search_scene.get("headers") or {}, search_scene.get("cookies") or []),
            # 保留原始 cookies 列表：请求时按目标域名过滤（全量 cookie 头会被部分子域拒为 400）。
            "cookies": search_scene.get("cookies") or [],
            "post_data_form": search_scene.get("post_data_form") or {},
            "post_data_json": search_scene.get("post_data_json"),
        },
        "inventory_export": {
            "url": export_scene.get("url"),
            "method": export_scene.get("method"),
            "headers": _sanitize_inventory_headers(export_scene.get("headers") or {}, export_scene.get("cookies") or []),
            "cookies": export_scene.get("cookies") or [],
            "post_data_form": export_scene.get("post_data_form") or {},
            "post_data_json": export_scene.get("post_data_json"),
        },
    }
    path = _template_path()
    write_json(path, template)
    return path


def _sanitize_inventory_headers(headers: dict[str, Any], cookies: list[dict[str, Any]] | None = None) -> dict[str, str]:
    cleaned = sanitize_replay_headers(headers, [])
    cookie_header = merge_cookie_header({}, cookies).get("cookie")
    if cookie_header:
        cleaned["cookie"] = trim_cookie_header(cookie_header)
    return cleaned


def _load_template() -> dict[str, Any]:
    path = _template_path()
    if not path.exists():
        raise RuntimeError(f"未找到猫超库存导出模板：{path}。请先运行 `ops tmcs inventory learn`。")
    return read_json(path)


def _write_adjust_template(*, search_scene: dict[str, Any], adjust_scene: dict[str, Any] | None = None) -> Path:
    headers = _sanitize_inventory_headers(search_scene.get("headers") or {}, search_scene.get("cookies") or [])
    template = {
        "site": TMCS_SITE,
        "scenes": {
            "inventory_search": TMCS_INVENTORY_SEARCH_SCENE,
            "inventory_adjust": TMCS_INVENTORY_ADJUST_SCENE,
        },
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "defaults": {
            "warehouse_code": DEFAULT_WAREHOUSE_CODE,
        },
        "inventory_search": {
            "url": search_scene.get("url"),
            "method": search_scene.get("method") or "POST",
            "headers": headers,
            "post_data_json": search_scene.get("post_data_json") or {},
        },
        "inventory_adjust": {
            "headers": _sanitize_inventory_headers((adjust_scene or search_scene).get("headers") or {}, (adjust_scene or search_scene).get("cookies") or []),
            "query_sellable_url": QUERY_SELLABLE_URL,
            "increase_url": INCREASE_ADJUST_URL,
            "decrease_url": DECREASE_ADJUST_URL,
            "_scm_token_": (search_scene.get("post_data_json") or {}).get("_scm_token_"),
        },
    }
    path = _adjust_template_path()
    write_json(path, template)
    return path


def _load_adjust_template() -> dict[str, Any]:
    path = _adjust_template_path()
    if path.exists():
        return read_json(path)
    base = _load_template()
    search_scene = base.get("inventory_search") or {}
    return {
        "site": TMCS_SITE,
        "scenes": {
            "inventory_search": TMCS_INVENTORY_SEARCH_SCENE,
            "inventory_adjust": TMCS_INVENTORY_ADJUST_SCENE,
        },
        "captured_at": base.get("captured_at"),
        "defaults": {
            "warehouse_code": ((base.get("defaults") or {}).get("warehouse_code") or DEFAULT_WAREHOUSE_CODE),
        },
        "inventory_search": search_scene,
        "inventory_adjust": {
            "headers": dict(search_scene.get("headers") or {}),
            "query_sellable_url": QUERY_SELLABLE_URL,
            "increase_url": INCREASE_ADJUST_URL,
            "decrease_url": DECREASE_ADJUST_URL,
            "_scm_token_": (search_scene.get("post_data_json") or {}).get("_scm_token_"),
        },
    }


def _apply_warehouse_code(value: Any, warehouse_code: str) -> Any:
    if isinstance(value, dict):
        updated = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in {
                "merchantwarehousecode",
                "merchantwhcode",
                "warehousecode",
                "merchant_warehouse_code",
                "storecode",
            }:
                updated[key] = [warehouse_code] if lowered == "storecode" else warehouse_code
            else:
                updated[key] = _apply_warehouse_code(nested, warehouse_code)
        return updated
    if isinstance(value, list):
        return [_apply_warehouse_code(item, warehouse_code) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return warehouse_code
        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return warehouse_code if text == DEFAULT_WAREHOUSE_CODE else value
            updated = _apply_warehouse_code(parsed, warehouse_code)
            return json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
        if value == DEFAULT_WAREHOUSE_CODE or value == "":
            return warehouse_code
    return value


def _prepare_form_data(form_data: dict[str, Any], warehouse_code: str) -> dict[str, str]:
    payload = _apply_warehouse_code(form_data, warehouse_code)
    return {str(key): str(value) for key, value in payload.items()}


def _has_warehouse_code(value: Any, warehouse_code: str) -> bool:
    if isinstance(value, dict):
        return any(_has_warehouse_code(item, warehouse_code) for item in value.values())
    if isinstance(value, list):
        return any(_has_warehouse_code(item, warehouse_code) for item in value)
    return warehouse_code in str(value)


def _parse_request_payload(request: Any) -> tuple[str | None, dict[str, Any] | None, dict[str, str] | None]:
    post_data = request.post_data
    post_data_json = request.post_data_json if post_data else None
    post_data_form: dict[str, str] | None = None
    if post_data and isinstance(post_data, str):
        parsed_form = dict(parse_qsl(post_data, keep_blank_values=True))
        if parsed_form:
            post_data_form = {str(key): str(value) for key, value in parsed_form.items()}
    return post_data, post_data_json if isinstance(post_data_json, dict) else None, post_data_form


def _build_scene_request(request: Any, *, source: str, primary_probe: dict[str, Any] | None = None) -> dict[str, Any]:
    post_data, post_data_json, post_data_form = _parse_request_payload(request)
    return {
        "site": TMCS_SITE,
        "scene": "",
        "status": "valid",
        "source": source,
        "url": request.url,
        "method": request.method.upper(),
        "headers": dict(request.headers),
        "post_data": post_data,
        "post_data_json": post_data_json,
        "post_data_form": post_data_form,
        "cookies": [],
        "meta": {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "primary_probe": primary_probe or {},
        },
    }


def _merge_primary_probe_shape(scene_request: dict[str, Any], primary_request: dict[str, Any] | None, warehouse_code: str) -> dict[str, Any]:
    if not primary_request:
        return scene_request
    if _has_warehouse_code(scene_request.get("post_data_form"), warehouse_code) or _has_warehouse_code(scene_request.get("post_data_json"), warehouse_code):
        return scene_request
    merged = dict(scene_request)
    if primary_request.get("post_data_form"):
        merged["post_data_form"] = _prepare_form_data(dict(primary_request["post_data_form"]), warehouse_code)
        merged["post_data"] = form_encode(merged["post_data_form"])
    elif primary_request.get("post_data_json"):
        merged["post_data_json"] = _apply_warehouse_code(primary_request["post_data_json"], warehouse_code)
        merged["post_data"] = json.dumps(merged["post_data_json"], ensure_ascii=False, separators=(",", ":"))
    return merged


def _fill_inventory_filters(page: Any, warehouse_code: str) -> None:
    candidates = [
        "input[placeholder*='商家仓']",
        "input[placeholder*='仓code']",
        "input[placeholder*='仓CODE']",
        "input[placeholder*='CODE']",
        "input[placeholder*='code']",
    ]
    for selector in candidates:
        locator = page.locator(selector).first
        try:
            if locator.count():
                locator.fill(warehouse_code, timeout=2000)
                return
        except Exception:
            continue
    try:
        if "商家仓code" in page.locator("body").inner_text(timeout=3000) and page.locator("input").count() >= 5:
            page.locator("input").nth(4).fill(warehouse_code, timeout=3000)
            return
    except Exception:
        pass
    try:
        filled = page.evaluate(
            """
            (warehouseCode) => {
              const texts = [...document.querySelectorAll('*')];
              for (const node of texts) {
                const text = (node.textContent || '').replace(/\\s+/g, '');
                if (!text.includes('商家仓code')) continue;
                const parent = node.closest('label,div,span,form,.next-form-item,.form-item') || node.parentElement;
                if (!parent) continue;
                const input = parent.querySelector('input') || parent.parentElement?.querySelector?.('input');
                if (!input) continue;
                input.focus();
                input.value = warehouseCode;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
              }
              return false;
            }
            """,
            warehouse_code,
        )
        if filled:
            return
    except Exception:
        pass
    raise RuntimeError("未找到“商家仓code”筛选输入框。请确认主浏览器/9222 当前页面就是一盘货库存管理页。")


def _click_button(page: Any, name: str, *, timeout_ms: int = 4000) -> None:
    strategies = [
        lambda: page.get_by_role("button", name=name).first.click(timeout=timeout_ms),
        lambda: page.get_by_text(name, exact=False).first.click(timeout=timeout_ms),
        lambda: page.locator(f"text={name}").first.click(timeout=timeout_ms),
    ]
    for action in strategies:
        try:
            action()
            return
        except Exception:
            continue
    raise RuntimeError(f"未找到按钮：{name}")


def _capture_inventory_requests(*, cdp_url: str, source: str, warehouse_code: str, primary_probe: dict[str, Any] | None = None) -> dict[str, Any]:
    from playwright.sync_api import Error as PlaywrightError  # type: ignore
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore

    captured: dict[str, Any] = {"search_request": None, "export_request": None}

    def on_request(request: Any) -> None:
        url = request.url
        method = request.method.upper()
        if method not in {"GET", "POST"}:
            return
        if SEARCH_URL_KEYWORD in url and captured["search_request"] is None:
            captured["search_request"] = _build_scene_request(request, source=source, primary_probe=primary_probe)
        elif EXPORT_URL_KEYWORD in url and method == "POST" and captured["export_request"] is None:
            captured["export_request"] = _build_scene_request(request, source=source, primary_probe=primary_probe)

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
        except PlaywrightError as exc:
            return {"status": "unavailable", "source": source, "cdp_url": cdp_url, "reason": str(exc)}
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        with managed_work_page(context, "tmcs.inventory.capture") as page:
            context.on("request", on_request)
            try:
                page.goto(INVENTORY_PAGE_URL, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                pass
            page.wait_for_timeout(3000)
            _fill_inventory_filters(page, warehouse_code)
            page.wait_for_timeout(500)
            _click_button(page, "查询")
            page.wait_for_timeout(2500)
            _click_button(page, "导出")
            deadline = time.time() + 25
            while time.time() < deadline:
                if captured["search_request"] and captured["export_request"]:
                    break
                page.wait_for_timeout(500)
            if not captured["search_request"] or not captured["export_request"]:
                return {
                    "status": "not_captured",
                    "source": source,
                    "cdp_url": cdp_url,
                    "warehouse_code": warehouse_code,
                    "reason": "未捕获到库存查询/导出请求",
                    "search_captured": bool(captured["search_request"]),
                    "export_captured": bool(captured["export_request"]),
                }
            cookies = context.cookies()
    captured["search_request"]["cookies"] = cookies
    captured["export_request"]["cookies"] = cookies
    return {"status": "captured", "source": source, "warehouse_code": warehouse_code, **captured}


def _capture_inventory_requests_raw_cdp(
    *,
    cdp_url: str,
    source: str,
    warehouse_code: str,
    primary_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    script = r"""
const input = JSON.parse(process.argv[1]);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class CDP {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = [];
  }
  async open() {
    await new Promise((resolve, reject) => {
      this.ws.addEventListener('open', resolve, { once: true });
      this.ws.addEventListener('error', reject, { once: true });
    });
    this.ws.addEventListener('message', (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result || {});
        return;
      }
      for (const handler of this.handlers) handler(msg);
    });
  }
  call(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (!this.pending.has(id)) return;
        this.pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }, 30000);
    });
  }
  on(handler) {
    this.handlers.push(handler);
  }
  close() {
    this.ws.close();
  }
}

function normalizeRequest(event) {
  return {
    url: event.request.url,
    method: event.request.method,
    headers: event.request.headers || {},
    post_data: event.request.postData || null,
    post_data_json: tryJson(event.request.postData),
    post_data_form: parseForm(event.request.postData),
  };
}

function tryJson(text) {
  if (!text || typeof text !== 'string') return null;
  try {
    const parsed = JSON.parse(text);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function parseForm(text) {
  if (!text || typeof text !== 'string' || !text.includes('=')) return null;
  const params = new URLSearchParams(text);
  const out = {};
  for (const [key, value] of params.entries()) out[key] = value;
  return Object.keys(out).length ? out : null;
}

const created = await fetch(`${input.cdpUrl.replace(/\/$/, '')}/json/new?${encodeURIComponent('about:blank')}`, { method: 'PUT' }).then((r) => r.json());
const baseCdpUrl = input.cdpUrl.replace(/\/$/, '');
const cdp = new CDP(created.webSocketDebuggerUrl);
await cdp.open();
async function closeCreatedTarget() {
  try { cdp.close(); } catch {}
  try { await fetch(`${baseCdpUrl}/json/close/${created.id}`); } catch {}
}
async function failAndExit(error) {
  await closeCreatedTarget();
  console.error(error?.stack || error);
  process.exit(1);
}
process.on('uncaughtException', failAndExit);
process.on('unhandledRejection', failAndExit);
let searchRequest = null;
let exportRequest = null;
cdp.on((msg) => {
  if (msg.method !== 'Network.requestWillBeSent') return;
  const url = msg.params?.request?.url || '';
  const method = msg.params?.request?.method || '';
  const postData = msg.params?.request?.postData || '';
  if (url.includes('listOneStockAllTubeInventory')) {
    if (!searchRequest || postData.includes(input.warehouseCode)) {
      searchRequest = normalizeRequest(msg.params);
    }
  }
  if (!exportRequest && method === 'POST' && url.includes('/gei/export/task/')) {
    exportRequest = normalizeRequest(msg.params);
  }
});
await cdp.call('Network.enable');
await cdp.call('Page.enable');
await cdp.call('Input.setIgnoreInputEvents', { ignore: false }).catch(() => {});
await cdp.call('Runtime.enable');
await cdp.call('Runtime.evaluate', {
  // marker 对齐 browser.managed_work_page 的 ops-cli:<owner>:<秒>:<rand8> 格式，
  // 让 9222 标签清理的"年龄保护"能识别本抓取标签、不误关在用页。
  expression: `window.name = 'ops-cli:tmcs.inventory.raw:' + Math.floor(Date.now() / 1000) + ':' + Math.random().toString(16).slice(2, 10)`,
  returnByValue: true,
}).catch(() => {});
await cdp.call('Page.navigate', { url: input.pageUrl });
await sleep(5000);
// 登录失效检测：未登录时会跳转到登录页，这里只读 URL 判定，命中则交回 Python 切前台。
async function currentUrl() {
  try {
    const r = await cdp.call('Runtime.evaluate', { returnByValue: true, expression: 'location.href' });
    return r.result?.value || '';
  } catch {
    return '';
  }
}
const navUrl = await currentUrl();
if (/login\./i.test(navUrl) || /\/login(\b|\/|\?)/i.test(navUrl)) {
  await closeCreatedTarget();
  console.log(JSON.stringify({
    status: 'login_required',
    source: input.source,
    current_url: navUrl,
    reason: '检测到猫超登录页，需要手动登录',
  }));
  process.exit(0);
}
async function rectFor(expression) {
  const result = await cdp.call('Runtime.evaluate', {
    awaitPromise: true,
    returnByValue: true,
    expression: `(() => {
      const el = ${expression};
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, width: rect.width, height: rect.height };
    })()`,
  });
  return result.result?.value || null;
}
async function clickAt(point) {
  await cdp.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y, button: 'none' });
  await cdp.call('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1 });
  await cdp.call('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1 });
}
const inputRect = await rectFor(`[...document.querySelectorAll('input')][4]`);
if (!inputRect) throw new Error('未找到商家仓code输入框 input[4]');
await clickAt(inputRect);
await cdp.call('Input.dispatchKeyEvent', { type: 'keyDown', modifiers: 8, key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, macCharCode: 0 });
await cdp.call('Input.dispatchKeyEvent', { type: 'keyUp', modifiers: 8, key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, macCharCode: 0 });
await cdp.call('Input.insertText', { text: input.warehouseCode });
await sleep(500);
const queryRect = await rectFor(`[...document.querySelectorAll('button')].find((el) => (el.textContent || '').trim() === '查询') || [...document.querySelectorAll('span,a,div')].find((el) => (el.textContent || '').trim() === '查询')`);
if (!queryRect) throw new Error('未找到查询按钮');
await clickAt(queryRect);
await sleep(4500);
const exportRect = await rectFor(`[...document.querySelectorAll('button')].find((el) => (el.textContent || '').trim() === '导出') || [...document.querySelectorAll('span,a,div')].find((el) => (el.textContent || '').trim() === '导出')`);
if (!exportRect) throw new Error('未找到导出按钮');
await clickAt(exportRect);
const deadline = Date.now() + 30000;
while (Date.now() < deadline && (!searchRequest || !exportRequest)) {
  await sleep(500);
}
let cookies = [];
try {
  const cookieResult = await cdp.call('Network.getAllCookies');
  cookies = cookieResult.cookies || [];
} catch {}
await closeCreatedTarget();
if (!searchRequest || !exportRequest) {
  console.log(JSON.stringify({
    status: 'not_captured',
    source: input.source,
    reason: '原生 CDP 未捕获到库存查询/导出请求',
    search_captured: Boolean(searchRequest),
    export_captured: Boolean(exportRequest),
  }));
} else {
  console.log(JSON.stringify({
    status: 'captured',
    source: input.source,
    warehouse_code: input.warehouseCode,
    search_request: { ...searchRequest, cookies },
    export_request: { ...exportRequest, cookies },
  }));
}
"""
    payload = {
        "cdpUrl": cdp_url,
        "source": source,
        "warehouseCode": warehouse_code,
        "pageUrl": INVENTORY_PAGE_URL,
    }
    completed = subprocess.run(
        ["node", "-e", script, json.dumps(payload, ensure_ascii=False)],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        return {
            "status": "unavailable",
            "source": source,
            "cdp_url": cdp_url,
            "reason": (completed.stderr or completed.stdout or "原生 CDP 执行失败").strip(),
        }
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": source,
            "cdp_url": cdp_url,
            "reason": f"原生 CDP 返回不可解析：{exc}; stdout={completed.stdout[-500:]}",
        }
    for key in ("search_request", "export_request"):
        if isinstance(result.get(key), dict):
            result[key] = _build_raw_cdp_scene_request(result[key], source=source, primary_probe=primary_probe)
    return result


def _build_raw_cdp_scene_request(request_data: dict[str, Any], *, source: str, primary_probe: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "site": TMCS_SITE,
        "scene": "",
        "status": "valid",
        "source": source,
        "url": request_data.get("url"),
        "method": str(request_data.get("method") or "POST").upper(),
        "headers": request_data.get("headers") or {},
        "post_data": request_data.get("post_data"),
        "post_data_json": request_data.get("post_data_json"),
        "post_data_form": request_data.get("post_data_form"),
        "cookies": request_data.get("cookies") or [],
        "meta": {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "capture_transport": "raw_cdp",
            "primary_probe": primary_probe or {},
        },
    }


def _probe_primary_chrome_inventory(*, warehouse_code: str = DEFAULT_WAREHOUSE_CODE) -> dict[str, Any]:
    cdp_url = get_config().primary_chrome_cdp_url.strip()
    if not cdp_url:
        path = _primary_probe_path()
        if path.exists():
            probe = read_json(path)
            probe.setdefault("source", "codex_chrome_extension")
            probe.setdefault("status", "captured")
            probe["probe_path"] = str(path)
            return probe
        return {
            "status": "missing_probe",
            "source": "primary_chrome",
            "reason": f"未找到主浏览器探测结果：{path}。请先用 Codex Chrome 插件在日常 Chrome 完成探测。",
        }
    result = _capture_inventory_requests(cdp_url=cdp_url, source="primary_chrome", warehouse_code=warehouse_code)
    if result.get("status") != "captured":
        return result
    return {
        "status": "captured",
        "source": "primary_chrome",
        "warehouse_code": warehouse_code,
        "search_request": _build_primary_probe_request_proxy(result["search_request"]),
        "export_request": _build_primary_probe_request_proxy(result["export_request"]),
        "meta": {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "trigger_steps": ["打开一盘货库存管理页", f"填写商家仓code={warehouse_code}", "点击查询", "点击导出"],
        },
    }


def _build_primary_probe_request_proxy(request_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": request_data.get("url"),
        "method": request_data.get("method"),
        "header_names": sorted(str(key) for key in (request_data.get("headers") or {}).keys()),
        "post_data": request_data.get("post_data"),
        "post_data_json": request_data.get("post_data_json"),
        "post_data_form": request_data.get("post_data_form"),
    }


def _capture_inventory_scenes(
    *,
    warehouse_code: str = DEFAULT_WAREHOUSE_CODE,
    force: bool = False,
    primary_probe: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    root = _sessionhub_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scene.chrome_cdp import CDP_URL, start_chrome, surface_for_login  # type: ignore

    ok, msg = start_chrome()
    if not ok:
        raise RuntimeError(msg)

    capture = _capture_inventory_requests_raw_cdp(
        cdp_url=CDP_URL,
        source="sessionhub_9222",
        warehouse_code=warehouse_code,
        primary_probe=primary_probe,
    )
    if capture.get("status") == "login_required":
        # 唯一允许弹窗的场景：把专用 9222 Chrome 切到前台让用户手动登录后重试。
        surface_for_login(
            "TMCS_LOGIN_REQUIRED：检测到猫超登录页，已将专用 Chrome 切到前台，请先完成登录后重试。"
        )
    if capture.get("status") != "captured":
        reason = capture.get("reason") or "9222 未捕获到库存请求"
        raise RuntimeError(f"9222 SessionHub 沉淀失败：{reason}")

    search_scene = _merge_primary_probe_shape(capture["search_request"], (primary_probe or {}).get("search_request"), warehouse_code)
    export_scene = _merge_primary_probe_shape(capture["export_request"], (primary_probe or {}).get("export_request"), warehouse_code)
    search_scene["scene"] = TMCS_INVENTORY_SEARCH_SCENE
    export_scene["scene"] = TMCS_INVENTORY_EXPORT_SCENE
    search_scene["meta"]["discovery_strategy"] = "primary_chrome_probe_then_sessionhub_9222_capture"
    export_scene["meta"]["discovery_strategy"] = "primary_chrome_probe_then_sessionhub_9222_capture"

    search_path = _scene_path(TMCS_INVENTORY_SEARCH_SCENE)
    export_path = _scene_path(TMCS_INVENTORY_EXPORT_SCENE)
    write_json(search_path, search_scene)
    write_json(export_path, export_scene)
    return search_scene, export_scene, str(search_path), str(export_path)


def _flatten_row(row: Any, *, prefix: str = "") -> dict[str, Any]:
    if not isinstance(row, dict):
        return {prefix or "value": row}
    flattened: dict[str, Any] = {}
    for key, value in row.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_row(value, prefix=name))
        elif isinstance(value, list):
            flattened[name] = json.dumps(value, ensure_ascii=False)
        else:
            flattened[name] = value
    return flattened


def _extract_inventory_rows(payload: Any) -> tuple[list[dict[str, Any]], int | None]:
    if not isinstance(payload, dict):
        return [], None
    data = payload.get("data")
    candidates = [payload, data] if isinstance(data, dict) else [payload]
    total: int | None = None
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("total", "totalCount", "count"):
            if isinstance(candidate.get(key), int):
                total = int(candidate[key])
                break
        for key in ("list", "rows", "dataSource", "items", "records"):
            rows = candidate.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)], total
    return [], total


def _parse_adjust_items(*, sku_adjust: list[str] | None, sku_id: str | None, item_id: str | None, quantity: int | None, action: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in sku_adjust or []:
        if ":" not in raw:
            raise RuntimeError(f"--sku-adjust 格式必须是 SKU:数量，例如 6247519890565:50，当前值：{raw}")
        sku, qty_text = raw.split(":", 1)
        sku = sku.strip()
        if not sku:
            raise RuntimeError(f"--sku-adjust 缺少 SKU：{raw}")
        try:
            qty = int(qty_text)
        except ValueError as exc:
            raise RuntimeError(f"--sku-adjust 数量必须是整数：{raw}") from exc
        items.append({"sku_id": sku, "quantity": qty})
    if sku_id or item_id:
        if action != "clear" and quantity is None:
            raise RuntimeError("使用 --sku-id 或 --item-id 时，increase/decrease 必须同时传入 --quantity。")
        items.append({"sku_id": sku_id, "item_id": item_id, "quantity": quantity})
    if not items:
        raise RuntimeError("请传入 --sku-id / --item-id，或一个以上 --sku-adjust SKU:数量。")
    for item in items:
        qty = item.get("quantity")
        if action != "clear" and (not isinstance(qty, int) or qty <= 0):
            raise RuntimeError("increase/decrease 的调整数量必须大于 0。")
    return items


def _trim_request_cookie(headers: dict[str, Any]) -> dict[str, str]:
    """裁掉超长 cookie 头中的非鉴权项，避免 nginx 400 Header Too Large。"""
    out = {str(key): str(value) for key, value in (headers or {}).items()}
    for key in list(out):
        if key.lower() == "cookie":
            out[key] = trim_cookie_header(out[key])
    return out


def _search_inventory_rows(*, search_scene: dict[str, Any], warehouse_code: str, sku_id: str | None = None, item_id: str | None = None) -> list[dict[str, Any]]:
    headers = dict(search_scene.get("headers") or {})
    method = str(search_scene.get("method") or "POST").upper()
    url = str(search_scene.get("url") or "").strip()
    # 原始 scene 经 9222 raw CDP 沉淀时 headers 不含 Cookie（requestWillBeSent 不带），
    # cookie 落在 scene 的 cookies 列表里；按 url 域名过滤后补全（全量 cookie 头会触发 400）。
    # 模板路径 headers 已自带 cookie、无 cookies 列表，cookie_header 为空时保持不变。
    scene_cookie = merge_cookie_header({}, filter_cookies_for_url(search_scene.get("cookies") or [], url)).get("cookie")
    if scene_cookie:
        headers = {key: value for key, value in headers.items() if key.lower() != "cookie"}
        headers["cookie"] = scene_cookie
    # 模板回退路径的 cookie 头可能是整套 *.tmall.com 全量 cookie（几万字符），裁到鉴权必需项。
    headers = _trim_request_cookie(headers)
    payload = _apply_warehouse_code(dict(search_scene.get("post_data_json") or {}), warehouse_code)
    payload["pageIndex"] = 1
    payload["pageSize"] = 50
    payload["storeCode"] = [warehouse_code]
    if sku_id:
        payload["skuId"] = [str(sku_id)]
    if item_id:
        payload["itemId"] = [str(item_id)]
    _, body, _ = tmcs_request(method, url, headers=headers, json_body=payload)
    rows, _ = _extract_inventory_rows(body)
    filtered = []
    for row in rows:
        if str(row.get("storeCode") or row.get("upperStoreCode") or "") != warehouse_code:
            continue
        if sku_id and str(row.get("skuId")) != str(sku_id):
            continue
        if item_id and str(row.get("itemId")) != str(item_id):
            continue
        filtered.append(row)
    return filtered


def _query_sellable_quantity(*, adjust_scene: dict[str, Any], row: dict[str, Any]) -> int:
    payload = {
        "scItemId": str(row.get("scItemId")),
        "storeCode": str(row.get("downStoreCode")),
        "downStoreCode": str(row.get("downStoreCode")),
        "userId": str(row.get("userId")),
        "principalId": str(row.get("userId")),
    }
    token = str(adjust_scene.get("_scm_token_") or "") or _extract_scm_token(adjust_scene.get("headers") or {})
    if token:
        payload["_scm_token_"] = token
    _, body, _ = tmcs_request(
        "POST",
        str(adjust_scene.get("query_sellable_url") or QUERY_SELLABLE_URL),
        headers=_trim_request_cookie(adjust_scene.get("headers") or {}),
        json_body=payload,
    )
    data = body.get("data") if isinstance(body, dict) else None
    try:
        return int(data)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"库存可售数量接口返回异常：{json.dumps(body, ensure_ascii=False)[:500]}") from exc


def _extract_scm_token(headers: dict[str, Any]) -> str | None:
    cookie = ""
    for key, value in headers.items():
        if str(key).lower() == "cookie":
            cookie = str(value)
            break
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name in {"_scm_token_", "SCM_TOKEN"} and value:
            return value
    return None


def _adjust_inventory_row(
    *,
    adjust_scene: dict[str, Any],
    row: dict[str, Any],
    action: str,
    quantity: int | None,
    execute: bool,
) -> dict[str, Any]:
    current_total = int(row.get("exclusiveInvQuantity") or 0)
    current_sellable = _query_sellable_quantity(adjust_scene=adjust_scene, row=row)
    plan_quantity = current_sellable if action == "clear" else int(quantity or 0)
    if action == "decrease" and plan_quantity > current_sellable:
        raise RuntimeError(f"SKU {row.get('skuId')} 当前最多可减少 {current_sellable}，不能减少 {plan_quantity}。")
    if action == "clear" and current_sellable <= 0:
        raise RuntimeError(f"SKU {row.get('skuId')} 当前可售库存为 0，不能扣减全部。")
    target_total = current_total + plan_quantity if action == "increase" else max(current_total - plan_quantity, 0)
    payload = {
        "downItemId": int(row.get("itemId")),
        "downSkuId": int(row.get("skuId")),
        "upperStoreCode": str(row.get("storeCode") or row.get("upperStoreCode")),
        "downStoreCode": str(row.get("downStoreCode")),
        "planQuantity": plan_quantity,
    }
    token = str(adjust_scene.get("_scm_token_") or "") or _extract_scm_token(adjust_scene.get("headers") or {})
    if token:
        payload["_scm_token_"] = token
    endpoint = str(adjust_scene.get("increase_url") or INCREASE_ADJUST_URL) if action == "increase" else str(adjust_scene.get("decrease_url") or DECREASE_ADJUST_URL)
    result: dict[str, Any] | None = None
    if execute:
        _, body, _ = tmcs_request("POST", endpoint, headers=_trim_request_cookie(adjust_scene.get("headers") or {}), json_body=payload)
        if not isinstance(body, dict) or body.get("success") is not True:
            raise RuntimeError(f"库存调整提交失败：{json.dumps(body, ensure_ascii=False)[:500]}")
        result = body
    return {
        "item_id": str(row.get("itemId")),
        "sku_id": str(row.get("skuId")),
        "warehouse_code": str(row.get("storeCode") or row.get("upperStoreCode")),
        "platform_warehouse_code": str(row.get("downStoreCode")),
        "sc_item_id": str(row.get("scItemId")),
        "action": action,
        "plan_quantity": plan_quantity,
        "before_total": current_total,
        "before_sellable": current_sellable,
        "expected_after_total": target_total,
        "endpoint": endpoint,
        "submitted": execute,
        "response": result,
    }


def _download_inventory_from_search_scene(*, search_scene: dict[str, Any], output_dir: Path, warehouse_code: str) -> dict[str, Any]:
    from openpyxl import Workbook

    headers = dict(search_scene.get("headers") or {})
    method = str(search_scene.get("method") or "POST").upper()
    url = str(search_scene.get("url") or "").strip()
    # 原始 scene 经 9222 raw CDP 沉淀时 headers 不含 Cookie（requestWillBeSent 不带），
    # cookie 落在 scene 的 cookies 列表里；按 url 域名过滤后补全（全量 cookie 头会触发 400）。
    # 模板路径 headers 已自带 cookie、无 cookies 列表，cookie_header 为空时保持不变。
    scene_cookie = merge_cookie_header({}, filter_cookies_for_url(search_scene.get("cookies") or [], url)).get("cookie")
    if scene_cookie:
        headers = {key: value for key, value in headers.items() if key.lower() != "cookie"}
        headers["cookie"] = scene_cookie
    payload = _apply_warehouse_code(dict(search_scene.get("post_data_json") or {}), warehouse_code)
    payload["pageSize"] = max(int(payload.get("pageSize") or 100), 100)
    all_rows: list[dict[str, Any]] = []
    total: int | None = None
    for page_index in range(1, 1000):
        payload["pageIndex"] = page_index
        _, page_payload, _ = tmcs_request(method, url, headers=headers, json_body=payload)
        rows, detected_total = _extract_inventory_rows(page_payload)
        total = detected_total if detected_total is not None else total
        if not rows:
            break
        all_rows.extend(rows)
        if total is not None and len(all_rows) >= total:
            break
        if len(rows) < int(payload["pageSize"]):
            break
    if not all_rows:
        raise RuntimeError("库存列表接口未返回可写入 Excel 的数据")

    flattened_rows = [_flatten_row(row) for row in all_rows]
    headers_out = sorted({key for row in flattened_rows for key in row.keys()})
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_path(output_dir / TMCS_INVENTORY_EXPORT_FILENAME)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "库存列表"
    sheet.append(headers_out)
    for row in flattened_rows:
        sheet.append([row.get(key) for key in headers_out])
    workbook.save(output_path)
    return {
        "output_path": str(output_path),
        "status_code": 200,
        "export_task_id": None,
        "download_url": None,
        "download_size": output_path.stat().st_size,
        "source": "inventory_search_api_fallback",
        "row_count": len(all_rows),
    }


def _download_inventory_export(*, search_scene: dict[str, Any], export_scene: dict[str, Any], output_dir: Path, warehouse_code: str) -> dict[str, Any]:
    form_data = _prepare_form_data(dict(export_scene.get("post_data_form") or {}), warehouse_code)
    headers = dict(export_scene.get("headers") or {})
    method = str(export_scene.get("method") or "POST").upper()
    url = str(export_scene.get("url") or "").strip()
    # 导出与下载走 tools.cbbs.tmall.com，与查询(aic.cbbs.tmall.com)不同域：
    # 必须按该域名过滤 cookie，全量 cookie 头(数百条)会被该域拒为 400。
    # 与 _search_inventory_rows / _download_inventory_from_search_scene 同一处理方式。
    scene_cookie = merge_cookie_header({}, filter_cookies_for_url(export_scene.get("cookies") or [], url)).get("cookie")
    if scene_cookie:
        headers = {key: value for key, value in headers.items() if key.lower() != "cookie"}
        headers["cookie"] = scene_cookie
    status_code, payload, _ = tmcs_request(
        method,
        url,
        headers=headers,
        data_body=form_encode(form_data),
    )
    task_id = extract_export_task_id(payload)
    download_url = find_download_url(payload)
    if task_id:
        gei_url = gei_task_download_url(url, task_id)
        content = b""
        resolved_url = None
        nested_payload: Any = None
        for attempt in range(1, 25):
            _, nested_payload, nested_content = tmcs_download(gei_url, headers=headers)
            try:
                content, resolved_url = resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=headers)
                break
            except RuntimeError as exc:
                message = str(exc)
                if "任务未生成文件" not in message and "00105" not in message:
                    raise
                if attempt == 24:
                    return _download_inventory_from_search_scene(
                        search_scene=search_scene,
                        output_dir=output_dir,
                        warehouse_code=warehouse_code,
                    )
                time.sleep(5)
    elif download_url:
        _, nested_payload, nested_content = tmcs_download(download_url, headers=headers)
        content, resolved_url = resolve_download_content(content=nested_content, parsed_payload=nested_payload, headers=headers)
    else:
        raise RuntimeError(f"库存导出接口未返回 taskId 或下载地址：{json.dumps(payload, ensure_ascii=False)[:500]}")
    if not content or not is_probably_excel(content):
        raise RuntimeError("库存导出返回的不是合法 Excel 文件")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_path(output_dir / TMCS_INVENTORY_EXPORT_FILENAME)
    output_path.write_bytes(content)
    return {
        "output_path": str(output_path),
        "status_code": status_code,
        "export_task_id": task_id,
        "download_url": resolved_url or download_url,
        "download_size": len(content),
    }


def learn_inventory_export(*, force: bool = False) -> CommandResponse:
    inputs = {
        "site": TMCS_SITE,
        "scenes": [TMCS_INVENTORY_SEARCH_SCENE, TMCS_INVENTORY_EXPORT_SCENE],
        "force": force,
        "warehouse_code": DEFAULT_WAREHOUSE_CODE,
    }
    primary_probe = _probe_primary_chrome_inventory(warehouse_code=DEFAULT_WAREHOUSE_CODE)
    if primary_probe.get("status") != "captured":
        # 日常 Chrome 探测仅为请求形状的可选提示；缺失时回退纯 9222 沉淀。
        # _capture_inventory_scenes 的原生 CDP 会自驱动库存页（填仓 code/查询/导出），
        # 检测到登录页时把专用 9222 Chrome 切前台让用户登录，无需依赖日常 Chrome 探测。
        primary_probe = {
            "status": "skipped",
            "source": "sessionhub_9222_only",
            "reason": primary_probe.get("reason") or "未做日常Chrome探测，回退纯9222沉淀",
        }
    search_scene, export_scene, search_path, export_path = _capture_inventory_scenes(
        warehouse_code=DEFAULT_WAREHOUSE_CODE,
        force=force,
        primary_probe=primary_probe,
    )
    template_path = _write_template(search_scene=search_scene, export_scene=export_scene)
    context_path = write_runtime_context(
        task_name="tmcs_inventory_learn",
        status="success",
        inputs=inputs,
        outputs={
            "template_path": str(template_path),
            "inventory_search_scene_path": search_path,
            "inventory_export_scene_path": export_path,
            "primary_probe": primary_probe,
        },
        artifacts=[search_path, export_path, str(template_path)],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="inventory learn",
        data={
            "site": TMCS_SITE,
            "inventory_search_scene": TMCS_INVENTORY_SEARCH_SCENE,
            "inventory_export_scene": TMCS_INVENTORY_EXPORT_SCENE,
            "primary_probe": primary_probe,
            "template_path": str(template_path),
            "context_path": str(context_path),
            "next_command": "ops --json tmcs inventory export",
        },
    )


def learn_inventory_adjust(*, force: bool = False) -> CommandResponse:
    inputs = {
        "site": TMCS_SITE,
        "scenes": [TMCS_INVENTORY_SEARCH_SCENE, TMCS_INVENTORY_ADJUST_SCENE],
        "force": force,
        "warehouse_code": DEFAULT_WAREHOUSE_CODE,
    }
    primary_probe = _probe_primary_chrome_inventory(warehouse_code=DEFAULT_WAREHOUSE_CODE)
    if primary_probe.get("status") != "captured":
        # 日常 Chrome 探测仅为请求形状的可选提示；缺失时回退纯 9222 沉淀。
        # _capture_inventory_scenes 的原生 CDP 会自驱动库存页（填仓 code/查询/导出），
        # 检测到登录页时把专用 9222 Chrome 切前台让用户登录，无需依赖日常 Chrome 探测。
        primary_probe = {
            "status": "skipped",
            "source": "sessionhub_9222_only",
            "reason": primary_probe.get("reason") or "未做日常Chrome探测，回退纯9222沉淀",
        }
    search_scene, _, search_path, _ = _capture_inventory_scenes(
        warehouse_code=DEFAULT_WAREHOUSE_CODE,
        force=force,
        primary_probe=primary_probe,
    )
    adjust_scene = dict(search_scene)
    adjust_scene["scene"] = TMCS_INVENTORY_ADJUST_SCENE
    adjust_scene["url"] = INCREASE_ADJUST_URL
    adjust_scene.setdefault("meta", {})
    adjust_scene["meta"]["discovery_strategy"] = "primary_chrome_probe_then_sessionhub_9222_capture"
    adjust_scene["meta"]["trigger_steps"] = ["查询指定 SKU", "点击专享现货库存修改", "选择增加/减少/扣减全部", "提交库存变更申请单"]
    adjust_path = _scene_path(TMCS_INVENTORY_ADJUST_SCENE)
    write_json(adjust_path, adjust_scene)
    template_path = _write_adjust_template(search_scene=search_scene, adjust_scene=adjust_scene)
    context_path = write_runtime_context(
        task_name="tmcs_inventory_adjust_learn",
        status="success",
        inputs=inputs,
        outputs={
            "template_path": str(template_path),
            "inventory_search_scene_path": search_path,
            "inventory_adjust_scene_path": str(adjust_path),
            "primary_probe": primary_probe,
        },
        artifacts=[search_path, str(adjust_path), str(template_path)],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="inventory adjust learn",
        data={
            "site": TMCS_SITE,
            "inventory_search_scene": TMCS_INVENTORY_SEARCH_SCENE,
            "inventory_adjust_scene": TMCS_INVENTORY_ADJUST_SCENE,
            "primary_probe": primary_probe,
            "template_path": str(template_path),
            "context_path": str(context_path),
            "next_command": "ops --json tmcs inventory adjust --sku-id 6247519890565 --action increase --quantity 50 --execute",
        },
    )


def run_inventory_export(
    *,
    warehouse_code: str = DEFAULT_WAREHOUSE_CODE,
    dry_run: bool = False,
) -> CommandResponse:
    template = _load_template()
    defaults = template.get("defaults") or {}
    output_dir = Path(str(defaults.get("output_dir") or get_config().tmcs_bill_download_dir)).expanduser()
    effective_warehouse_code = warehouse_code or str(defaults.get("warehouse_code") or DEFAULT_WAREHOUSE_CODE)

    scene_warnings: list[str] = []
    for scene_name in (TMCS_INVENTORY_SEARCH_SCENE, TMCS_INVENTORY_EXPORT_SCENE):
        try:
            check_scene_or_fail(TMCS_SITE, scene_name, next_command="ops tmcs inventory learn")
        except RuntimeError as exc:
            if recovery_must_fail_fast():
                raise
            scene_warnings.append(str(exc))

    if dry_run:
        preview_output = str(output_dir / TMCS_INVENTORY_EXPORT_FILENAME)
        context_path = write_runtime_context(
            task_name="tmcs_inventory_export_run",
            status="success",
            inputs={"warehouse_code": effective_warehouse_code, "dry_run": True},
            outputs={"warehouse_code": effective_warehouse_code, "output_dir": str(output_dir), "output_path": preview_output, "downloaded": False, "scene_warnings": scene_warnings},
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="inventory export",
            data={
                "warehouse_code": effective_warehouse_code,
                "scene": TMCS_INVENTORY_EXPORT_SCENE,
                "search_scene": TMCS_INVENTORY_SEARCH_SCENE,
                "output_dir": str(output_dir),
                "output_path": preview_output,
                "downloaded": False,
                "scene_warnings": scene_warnings,
                "dry_run": True,
                "context_path": str(context_path),
            },
        )

    # 模板 headers 只带全量 cookie 串，下载需按目标域名过滤 cookie；原始 cookies 列表在 scene 文件里。
    # 旧模板（无 cookies 字段）从 scene 文件补全，避免依赖易碎的重新 learn 页面驱动。
    search_section = dict(template.get("inventory_search") or {})
    export_section = dict(template.get("inventory_export") or {})
    for section, scene_name in ((search_section, TMCS_INVENTORY_SEARCH_SCENE), (export_section, TMCS_INVENTORY_EXPORT_SCENE)):
        if not section.get("cookies"):
            scene_path = _scene_path(scene_name)
            if scene_path.exists():
                try:
                    section["cookies"] = read_json(scene_path).get("cookies") or []
                except Exception:  # noqa: BLE001 - cookies 补全失败不阻断，后续按需报错
                    pass

    export_result = _download_inventory_export(
        search_scene=search_section,
        export_scene=export_section,
        output_dir=output_dir,
        warehouse_code=effective_warehouse_code,
    )
    context_path = write_runtime_context(
        task_name="tmcs_inventory_export_run",
        status="success",
        inputs={"warehouse_code": effective_warehouse_code, "dry_run": False},
        outputs={"warehouse_code": effective_warehouse_code, "downloaded": True, **export_result},
        artifacts=[str(export_result["output_path"])],
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="inventory export",
        data={
            "warehouse_code": effective_warehouse_code,
            "scene": TMCS_INVENTORY_EXPORT_SCENE,
            "search_scene": TMCS_INVENTORY_SEARCH_SCENE,
            "downloaded": True,
            "context_path": str(context_path),
            **export_result,
        },
    )


def run_inventory_adjust(
    *,
    action: str,
    sku_id: str | None = None,
    item_id: str | None = None,
    quantity: int | None = None,
    sku_adjust: list[str] | None = None,
    warehouse_code: str = DEFAULT_WAREHOUSE_CODE,
    execute: bool = False,
) -> CommandResponse:
    normalized_action = action.strip().lower().replace("_", "-")
    action_map = {
        "increase": "increase",
        "add": "increase",
        "decrease": "decrease",
        "reduce": "decrease",
        "clear": "clear",
        "clear-all": "clear",
        "decrease-all": "clear",
    }
    if normalized_action not in action_map:
        raise RuntimeError("--action 只支持 increase、decrease、clear。")
    effective_action = action_map[normalized_action]
    template = _load_adjust_template()
    defaults = template.get("defaults") or {}
    effective_warehouse_code = warehouse_code or str(defaults.get("warehouse_code") or DEFAULT_WAREHOUSE_CODE)
    search_scene = template.get("inventory_search") or {}
    adjust_scene = template.get("inventory_adjust") or {}
    items = _parse_adjust_items(
        sku_adjust=sku_adjust,
        sku_id=sku_id,
        item_id=item_id,
        quantity=quantity,
        action=effective_action,
    )

    scene_warnings: list[str] = []
    for scene_name in (TMCS_INVENTORY_SEARCH_SCENE,):
        try:
            check_scene_or_fail(TMCS_SITE, scene_name, next_command="ops tmcs inventory learn")
        except RuntimeError as exc:
            if recovery_must_fail_fast():
                raise
            scene_warnings.append(str(exc))

    results: list[dict[str, Any]] = []
    for item in items:
        rows = _search_inventory_rows(
            search_scene=search_scene,
            warehouse_code=effective_warehouse_code,
            sku_id=item.get("sku_id"),
            item_id=item.get("item_id"),
        )
        if not rows:
            raise RuntimeError(f"未查询到库存记录：sku_id={item.get('sku_id')} item_id={item.get('item_id')} warehouse={effective_warehouse_code}")
        for row in rows:
            results.append(
                _adjust_inventory_row(
                    adjust_scene=adjust_scene,
                    row=row,
                    action=effective_action,
                    quantity=item.get("quantity"),
                    execute=execute,
                )
            )

    context_path = write_runtime_context(
        task_name="tmcs_inventory_adjust_run",
        status="success",
        inputs={
            "action": effective_action,
            "sku_id": sku_id,
            "item_id": item_id,
            "quantity": quantity,
            "sku_adjust": sku_adjust or [],
            "warehouse_code": effective_warehouse_code,
            "execute": execute,
        },
        outputs={
            "scene": TMCS_INVENTORY_ADJUST_SCENE,
            "search_scene": TMCS_INVENTORY_SEARCH_SCENE,
            "adjusted_count": len(results),
            "results": results,
            "scene_warnings": scene_warnings,
        },
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="inventory adjust",
        data={
            "warehouse_code": effective_warehouse_code,
            "scene": TMCS_INVENTORY_ADJUST_SCENE,
            "search_scene": TMCS_INVENTORY_SEARCH_SCENE,
            "action": effective_action,
            "execute": execute,
            "submitted": execute,
            "adjusted_count": len(results),
            "results": results,
            "scene_warnings": scene_warnings,
            "context_path": str(context_path),
        },
    )
