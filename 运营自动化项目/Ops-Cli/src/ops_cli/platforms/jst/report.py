"""JST 报表导出能力：胜算 → 报表 → 商品利润 → 导出「商品销售情况.csv」。

平台边界：CSV 下载动作只允许在这里（Ops-Cli）发生。业务层（运营自动化工具）只消费
`ops --json jst report product-profit export` 的统一 JSON，不直接请求聚水潭。

dry-run（默认，或未显式 --execute）：只校验参数、规范化月份/门店、给出将要执行的动作预览，
返回 simulated=true，绝不产生真实平台导出任务、不写文件。

execute（--execute 且非 --dry-run）：在配置的下载目录拾取最近一次浏览器导出的
「商品销售情况*.csv」（与 jst product 同样的"消费浏览器导出文件"口径，下载动作仍属平台层），
复制到目标路径并返回 csv_path。若没有可用导出文件，给出清晰错误提示。
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ops_cli.config import get_config
from ops_cli.output import CommandResponse
from ops_cli.platforms.jst.shared import surface_jst_login_if_needed
from ops_cli.platforms.jst.shops import default_shop
from ops_cli.runtime_context import write_runtime_context


PROFIT_REPORT_SCENE = "business_profit_multi_dimension_report"
GOODS_PROFIT_EXPORT_SCENE = "goods_profit_export"
GOODS_PROFIT_URL = "https://ss.erp321.com/profit-report/goods-profit"
# 默认店铺名来自统一注册表（启明工贸）。报表门店下拉用子串匹配，注册全名是子串兜底。
DEFAULT_SHOP_NAME = default_shop().shop_name
TARGET_FILENAME = "商品销售情况.csv"
EXPORT_GLOB = "*商品销售情况*.csv"
RECENT_EXPORT_MAX_AGE_SECONDS = 3600
# 导出相关请求 URL 关键字（用于 9222 抓包识别导出端点）
EXPORT_URL_HINTS = ("xport", "Export", "excel", "Excel", "Goods", "GoodsProfit", "download", "Download", "task", "Task")


def _sessionhub_root() -> Path:
    return Path(get_config().sessionhub_root).expanduser().resolve()


def _scene_store_path(scene: str) -> Path:
    return _sessionhub_root() / "data" / "sessions" / "jst_erp" / f"{scene}.json"


def _today() -> date:
    return date.today()


def _last_month() -> str:
    first_of_this_month = _today().replace(day=1)
    last_month_end = first_of_this_month - timedelta(days=1)
    return last_month_end.strftime("%Y-%m")


def _normalize_month(month_value: str) -> tuple[str, date, date]:
    try:
        month_start = date.fromisoformat(f"{month_value}-01")
    except ValueError as exc:
        raise RuntimeError("月份只支持 YYYY-MM") from exc
    normalized = month_start.strftime("%Y-%m")
    if normalized != month_value:
        raise RuntimeError("月份只支持 YYYY-MM")
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    month_end = next_month - timedelta(days=1)
    return normalized, month_start, month_end


def _normalize_period(
    *, month: str | None = None, start_date: str | None = None, end_date: str | None = None
) -> tuple[str | None, date, date, str]:
    if start_date or end_date:
        if not start_date or not end_date:
            raise RuntimeError("--start-date 和 --end-date 必须同时提供")
        try:
            begin = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise RuntimeError("日期只支持 YYYY-MM-DD") from exc
        if begin > end:
            raise RuntimeError("--start-date 不能晚于 --end-date")
        return None, begin, end, f"{begin.isoformat()}_to_{end.isoformat()}"

    resolved_month = (month or _last_month()).strip()
    normalized_month, month_start, month_end = _normalize_month(resolved_month)
    return normalized_month, month_start, month_end, normalized_month


def _download_dir(override: str | None) -> Path:
    if override:
        return Path(override).expanduser()
    return Path(get_config().tmcs_bill_download_dir).expanduser()


def _find_recent_export_csv(download_dir: Path, *, max_age_seconds: int = RECENT_EXPORT_MAX_AGE_SECONDS) -> Path | None:
    if not download_dir.is_dir():
        return None
    cutoff = time.time() - max_age_seconds
    candidates = [
        path
        for path in download_dir.glob(EXPORT_GLOB)
        if path.is_file() and path.stat().st_mtime >= cutoff
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _resolve_dest(dest: str | None, *, period_label: str) -> Path:
    if dest:
        candidate = Path(dest).expanduser()
        if candidate.is_dir() or str(candidate).endswith("/"):
            return candidate / f"商品销售情况_{period_label}.csv"
        return candidate
    base = Path.cwd() / "output" / "jst_report"
    return base / f"商品销售情况_{period_label}.csv"


def _last_month_number() -> int:
    first_of_this_month = _today().replace(day=1)
    return (first_of_this_month - timedelta(days=1)).month


def _apply_goods_profit_filters(page: Any, *, shop_name: str, begin: date, end: date, month_number: int | None) -> None:
    """在商品利润页应用门店 + 月份筛选（基于主浏览器探测得到的真实 UI 流程）。"""
    # 等报表筛选栏就绪（#shop 是门店筛选输入框）。慢加载/登录跳转时给足时间。
    try:
        page.wait_for_selector("#shop", state="visible", timeout=30000)
    except Exception:
        try:
            (Path.cwd() / "sandbox").mkdir(exist_ok=True)
            page.screenshot(path=str(Path.cwd() / "sandbox" / "_filters_notready.png"))
        except Exception:
            pass
        raise RuntimeError("商品利润报表筛选栏未就绪（#shop 未出现，可能未登录/未加载，已截图 sandbox/_filters_notready.png）")
    page.wait_for_timeout(1200)
    # 店铺：打开筛选弹窗 -> 勾选目标猫超店铺 -> 确定
    page.locator("#shop").click(timeout=8000)
    page.wait_for_timeout(1000)
    page.get_by_text(shop_name, exact=False).first.click(timeout=8000)
    for confirm_text in ("确 定", "确定"):
        try:
            page.get_by_text(confirm_text, exact=True).click(timeout=2500)
            break
        except Exception:
            continue
    page.wait_for_timeout(1000)
    if month_number is not None:
        # 月份：日/周/月 是第二个 Radio.Button 组（ss-common-ss-picker-switch），「月」其实是
        # 一个 readonly 的 ant-picker 输入框 <input placeholder="月">，点它会切到月模式并弹出年月面板。
        toggle_dbg = {"clicked": False}
        try:
            page.locator('#goods-profit input[placeholder="月"]').first.click(timeout=6000)
            toggle_dbg = {"clicked": True}
        except Exception as exc:
            toggle_dbg = {"clicked": False, "err": str(exc)[:80]}
        page.wait_for_timeout(1000)
        # 年月面板里点目标月份单元格（antd month panel：.ant-picker-cell-inner 文本如「5月」）
        want_cell = f"{month_number}月"
        cell_dbg = page.evaluate(
            """(want) => {
              const norm = s => (s||'').replace(/\\s+/g,'');
              const panels = [...document.querySelectorAll('.ant-picker-month-panel, .ant-picker-panel')]
                .filter(p=>{const r=p.getBoundingClientRect(); return r.width>0&&r.height>0;});
              for (const p of panels) {
                const cells = [...p.querySelectorAll('.ant-picker-cell-inner, .ant-picker-cell')];
                const t = cells.find(c => norm(c.textContent) === want);
                if (t) { t.click(); return {clicked:true, panel:true}; }
              }
              const all = [...document.querySelectorAll('.ant-picker-cell-inner')];
              const t = all.find(c => norm(c.textContent) === want);
              if (t) { t.click(); return {clicked:true, panel:false}; }
              return {clicked:false, seen: all.slice(0,14).map(c=>norm(c.textContent))};
            }""",
            want_cell,
        )
    else:
        toggle_dbg = {"clicked": False, "mode": "date_range"}
        cell_dbg = page.evaluate(
            """([begin, end]) => {
              const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
              const setValue = (input, value) => {
                input.focus();
                input.value = value;
                input.dispatchEvent(new Event('input', {bubbles:true}));
                input.dispatchEvent(new Event('change', {bubbles:true}));
              };
              const root = document.querySelector('#goods-profit') || document;
              const inputs = [...root.querySelectorAll('input')].filter(visible);
              const rangeInputs = inputs.filter(i => ['开始日期','结束日期','日期','时间'].some(t => (i.placeholder||'').includes(t)));
              if (rangeInputs.length >= 2) {
                setValue(rangeInputs[0], begin);
                setValue(rangeInputs[1], end);
                return {clicked:true, filled:true, method:'range-inputs'};
              }
              const dateInput = inputs.find(i => ['日期','日'].some(t => (i.placeholder||'').includes(t)));
              if (dateInput) {
                dateInput.click();
                return {clicked:true, filled:false, method:'single-date-input'};
              }
              return {clicked:false, filled:false, placeholders: inputs.map(i => i.placeholder || '').slice(0, 12)};
            }""",
            [begin.isoformat(), end.isoformat()],
        )
    page.wait_for_timeout(1200)
    try:
        page.get_by_text("查 询", exact=True).click(timeout=3000)
    except Exception:
        try:
            page.get_by_text("查询", exact=True).click(timeout=2000)
        except Exception:
            pass
    page.wait_for_timeout(3500)
    # 读「时间范围」文本（跳过 style/script），核对月份是否生效
    try:
        rng = page.evaluate(
            """() => {
              const els = [...document.querySelectorAll('span,div,label')].filter(e =>
                (e.textContent||'').includes('时间范围') && e.children.length<=4 && e.tagName!=='STYLE' && e.tagName!=='SCRIPT');
              els.sort((a,b)=>(a.textContent||'').length-(b.textContent||'').length);
              return els.length ? (els[0].textContent||'').replace(/\\s+/g,' ').trim().slice(0,70) : '';
            }"""
        )
    except Exception:
        rng = ""
    return {"range": rng, "toggle": toggle_dbg, "cell": cell_dbg}


def probe_goods_profit_export(
    *,
    shop_name: str | None = None,
    month: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    dest: str | None = None,
) -> CommandResponse:
    """用 9222 Playwright 真实走商品利润导出，捕获导出端点 / 下载文件（学习用）。

    主浏览器只负责观察真实行为；这里在 9222 专用浏览器复刻、沉淀 scene，符合双浏览器口径。
    捕获两类信息：1) 导出相关网络请求（端点+payload，判断是否可纯 API 重放）；
    2) 浏览器 download 事件（异步任务式导出的兜底取件路径）。
    """
    resolved_shop = (shop_name or DEFAULT_SHOP_NAME).strip()
    normalized_month, period_start, period_end, period_label = _normalize_period(
        month=month, start_date=start_date, end_date=end_date
    )
    month_number = period_start.month if normalized_month else None

    root = _sessionhub_root()
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from scene.chrome_cdp import CDP_URL, start_chrome  # type: ignore
    from playwright.sync_api import Error as PlaywrightError  # type: ignore
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # type: ignore
    from playwright.sync_api import sync_playwright  # type: ignore

    ok, msg = start_chrome()
    if not ok:
        raise RuntimeError(msg)

    export_requests: list[dict[str, Any]] = []

    export_started = {"v": False}

    def on_request(request: Any) -> None:
        url = request.url
        method = request.method.upper()
        is_hint = any(hint in url for hint in EXPORT_URL_HINTS)
        # 导出点击之后的所有 WebApi POST 都记录，避免漏掉真实导出端点
        is_post_api = method == "POST" and "erp321.com/WebApi" in url
        if is_hint or (export_started["v"] and is_post_api):
            try:
                post = request.post_data
            except Exception:
                post = None
            export_requests.append({
                "url": url, "method": method,
                "after_export_click": export_started["v"],
                "post_data": (post or "")[:2000],
            })

    download_path: str | None = None
    download_name: str | None = None

    modal_buttons: list[dict[str, Any]] = []
    applied_range = ""
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except PlaywrightError as exc:
            raise RuntimeError(f"连接 9222 Chrome 失败：{exc}") from exc
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        # 防 9222 闪退：Chrome 在「窗口 0 标签页」时会关闭窗口。这里始终保证有一个
        # 轻量 about:blank 保活标签存活。先建保活页 + 工作页，再关历次残留的重页，
        # 收尾时也只关工作页、保活页留存，标签数永不归零。
        keepalive = context.new_page()
        try:
            keepalive.goto("about:blank", timeout=5000)
        except Exception:
            pass
        page = context.new_page()
        for stray in list(context.pages):
            if stray is page or stray is keepalive:
                continue
            try:
                stray.close()
            except Exception:
                pass
        context.on("request", on_request)
        # 把 9222 窗口拉宽，避免顶部日期工具栏（含 日/周/月/自定义）折叠进 overflow 菜单
        try:
            cdp = context.new_cdp_session(page)
            win = cdp.send("Browser.getWindowForTarget")
            cdp.send("Browser.setWindowBounds", {
                "windowId": win["windowId"],
                "bounds": {"left": 0, "top": 0, "width": 1680, "height": 1050, "windowState": "normal"},
            })
        except Exception:
            pass
        try:
            try:
                page.goto(GOODS_PROFIT_URL, wait_until="domcontentloaded", timeout=30000)
            except PlaywrightTimeoutError:
                pass
            surface_jst_login_if_needed(page)
            applied_range = _apply_goods_profit_filters(
                page, shop_name=resolved_shop, begin=period_start, end=period_end, month_number=month_number
            )

            export_started["v"] = True

            # ① 打开「导出数据」下拉：antd Dropdown 用 portal 渲染到 body 末尾的 .ant-dropdown
            # 节点，不是按钮子树。点击触发后用主世界 JS 检测 portal 出现。带重试 + hover 兜底。
            trigger = page.locator("button:visible", has_text="导出数据").first
            try:
                trigger.scroll_into_view_if_needed(timeout=4000)
            except Exception:
                pass

            def _dropdown_visible() -> bool:
                return bool(page.evaluate(
                    """() => {
                      const dds = document.querySelectorAll('.ant-dropdown');
                      for (const dd of dds) {
                        if (dd.classList.contains('ant-dropdown-hidden')) continue;
                        const r = dd.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) return true;
                      }
                      return false;
                    }"""
                ))

            dropdown_open = False
            for attempt in range(4):
                try:
                    trigger.hover(timeout=2000)
                except Exception:
                    pass
                try:
                    trigger.click(timeout=3000)
                except Exception:
                    pass
                page.wait_for_timeout(700)
                if _dropdown_visible():
                    dropdown_open = True
                    break
            if not dropdown_open:
                raise RuntimeError("导出数据 下拉始终未打开（trigger 重试 4 次）")

            # ② 在 portal 里点「导出数据」菜单项
            clicked_item = page.evaluate(
                """() => {
                  const dds = [...document.querySelectorAll('.ant-dropdown:not(.ant-dropdown-hidden)')]
                    .filter(el => { const r = el.getBoundingClientRect(); return r.width>0 && r.height>0; });
                  for (const dd of dds) {
                    const items = [...dd.querySelectorAll('.ant-dropdown-menu-item, li[role=menuitem]')];
                    const t = items.find(el => (el.innerText||'').trim() === '导出数据');
                    if (t) { t.click(); return true; }
                  }
                  return false;
                }"""
            )
            if not clicked_item:
                raise RuntimeError("portal 下拉里没找到「导出数据」菜单项")

            # ③ 等导出配置弹窗（antd Modal 用 portal 渲染；antd v4 里 .ant-modal 自身
            # 常常 0×0，需用 .ant-modal-content / .ant-modal-footer 这种确实有尺寸的层判定）
            modal_appeared = False
            for _ in range(25):
                page.wait_for_timeout(300)
                modal_appeared = bool(page.evaluate(
                    """() => {
                      const c = [...document.querySelectorAll('.ant-modal-content, .ant-modal-footer, .ant-modal-body')]
                        .find(el => { const r = el.getBoundingClientRect(); return r.width>0 && r.height>0; });
                      return !!c;
                    }"""
                ))
                if modal_appeared:
                    break
            if not modal_appeared:
                raise RuntimeError("导出配置弹窗未出现")

            # 导出弹窗保持默认（费用层级=末级利润表项目，其他都不勾），直接点「导 出」。
            # 列布局差异由业务层 csv_analyzer 的「按表头名归一化列」适配，无需在此选项。

            # 记录弹窗里「导出/取消」按钮（运维诊断用；按钮文本带空格如「导 出」「取 消」）
            try:
                modal_buttons = page.evaluate(
                    """() => {
                      const norm = s => (s||'').replace(/\\s+/g,'');
                      const all = [...document.querySelectorAll('button, a, [role=button], .ant-btn')];
                      const out = [];
                      for (const b of all) {
                        const n = norm(b.innerText);
                        if (n !== '导出' && n !== '取消') continue;
                        const r = b.getBoundingClientRect();
                        out.push({text:(b.innerText||'').trim(), tag:b.tagName, visible:(r.width>0&&r.height>0)});
                      }
                      return out;
                    }"""
                )
            except Exception as exc:
                modal_buttons = [{"error": str(exc)}]

            # ④ 点弹窗里蓝色「导出」确认按钮 → 触发下载（按钮文本可能是「导 出」带空格）
            def _click_export_confirm() -> None:
                ok = page.evaluate(
                    """() => {
                      const norm = s => (s||'').replace(/\\s+/g,'');
                      const all = [...document.querySelectorAll('button, .ant-btn, [role=button]')]
                        .filter(b => { const r=b.getBoundingClientRect(); return r.width>0 && r.height>0; });
                      // 只取确认按钮「导出」，排除触发器「导出数据」「导出明细数据」
                      const cands = all.filter(b => norm(b.innerText) === '导出');
                      // 优先 antd primary（蓝色确认）
                      let t = cands.find(b => /ant-btn-primary/.test(b.className||'')) || cands[0];
                      if (t) { t.click(); return true; }
                      return false;
                    }"""
                )
                if not ok:
                    raise RuntimeError("未能点中导出弹窗的「导出」确认按钮")

            try:
                with page.expect_download(timeout=60000) as dl_info:
                    _click_export_confirm()
                download = dl_info.value
                download_name = download.suggested_filename
                target = Path(dest).expanduser() if dest else (Path.cwd() / "output" / "jst_report" / f"商品销售情况_{period_label}.csv")
                target.parent.mkdir(parents=True, exist_ok=True)
                download.save_as(str(target))
                download_path = str(target)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(1500)
        finally:
            # 只关工作页；保活 about:blank 标签留存，确保 Chrome 窗口不被关闭（不闪退）
            try:
                if keepalive.is_closed():
                    keepalive = context.new_page()
                    keepalive.goto("about:blank", timeout=3000)
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass

    scene_payload = {
        "site": "jst_erp",
        "scene": GOODS_PROFIT_EXPORT_SCENE,
        "source": "sessionhub_9222",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "target_url": GOODS_PROFIT_URL,
        "shop_name": resolved_shop,
        "month": normalized_month,
        "period": {"begin": period_start.isoformat(), "end": period_end.isoformat()},
        "period_label": period_label,
        "export_requests": export_requests,
        "download_observed": download_path is not None,
        "download_name": download_name,
    }
    scene_path = _scene_store_path(GOODS_PROFIT_EXPORT_SCENE)
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    scene_path.write_text(json.dumps(scene_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return CommandResponse(
        success=True,
        platform="jst",
        command="report product-profit learn",
        data={
            "shop_name": resolved_shop,
            **({"month": normalized_month} if normalized_month else {}),
            "period": {"begin": period_start.isoformat(), "end": period_end.isoformat()},
            "period_label": period_label,
            "scene": GOODS_PROFIT_EXPORT_SCENE,
            "scene_path": str(scene_path),
            "download_observed": download_path is not None,
            "csv_path": download_path,
            "download_name": download_name,
            "export_requests": export_requests,
            "applied_range": applied_range,
            "modal_buttons": modal_buttons,
        },
    )


def export_product_profit_csv(
    *,
    shop_name: str | None = None,
    month: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = True,
    dest: str | None = None,
    download_dir: str | None = None,
) -> CommandResponse:
    """导出指定门店、指定月份或日期范围的「商品销售情况.csv」。"""
    resolved_shop = (shop_name or DEFAULT_SHOP_NAME).strip()
    normalized_month, period_start, period_end, period_label = _normalize_period(
        month=month, start_date=start_date, end_date=end_date
    )
    planned_action = (
        "胜算 → 报表 → 商品利润 → 选择门店 → 选择时间范围 → 查询 → 导出数据 → 下拉框「导出数据」"
    )

    if dry_run:
        context_path = write_runtime_context(
            task_name="jst_report_product_profit_export",
            status="success",
            inputs={
                "shop_name": resolved_shop,
                **({"month": normalized_month} if normalized_month else {}),
                "period": {"begin": period_start.isoformat(), "end": period_end.isoformat()},
                "dry_run": True,
            },
            outputs={"simulated": True, "downloaded": False},
        )
        data = {
            "shop_name": resolved_shop,
            "period": {"begin": period_start.isoformat(), "end": period_end.isoformat()},
            "period_label": period_label,
            "target_file": TARGET_FILENAME,
            "scene": PROFIT_REPORT_SCENE,
            "simulated": True,
            "downloaded": False,
            "planned_action": planned_action,
            "context_path": str(context_path),
            "dry_run": True,
        }
        if normalized_month:
            data["month"] = normalized_month
        return CommandResponse(
            success=True,
            platform="jst",
            command="report product-profit export",
            data=data,
        )

    # execute：真实下载走 9222 双浏览器自动化（进商品利润报表→筛选→导出弹窗→「导 出」→ 捕获下载）。
    destination = _resolve_dest(dest, period_label=period_label)
    probe = probe_goods_profit_export(
        shop_name=resolved_shop,
        month=normalized_month,
        start_date=start_date,
        end_date=end_date,
        dest=str(destination),
    )
    pdata = probe.data
    csv_path = pdata.get("csv_path")

    if not csv_path:
        # 9222 没拿到下载（如未登录/页面异常）时，兜底拾取下载目录里现成的导出文件
        resolved_download_dir = _download_dir(download_dir)
        recent = _find_recent_export_csv(resolved_download_dir)
        if recent is None:
            raise RuntimeError(
                "9222 自动导出未捕获到下载文件，且下载目录无现成「商品销售情况.csv」。"
                f"请确认 9222 已登录聚水潭；或在主浏览器完成 {planned_action} 后重试，"
                "或在业务层使用 --use-local-file 指定本地 CSV。"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(recent, destination)
        csv_path = str(destination)

    context_path = write_runtime_context(
        task_name="jst_report_product_profit_export",
        status="success",
        inputs={
            "shop_name": resolved_shop,
            **({"month": normalized_month} if normalized_month else {}),
            "period": {"begin": period_start.isoformat(), "end": period_end.isoformat()},
            "dry_run": False,
        },
        outputs={"csv_path": csv_path, "downloaded": True},
    )
    data = {
        "shop_name": resolved_shop,
        "period": {"begin": period_start.isoformat(), "end": period_end.isoformat()},
        "period_label": period_label,
        "target_file": TARGET_FILENAME,
        "scene": GOODS_PROFIT_EXPORT_SCENE,
        "simulated": False,
        "downloaded": True,
        "csv_path": csv_path,
        "download_name": pdata.get("download_name"),
        "applied_range": pdata.get("applied_range", {}).get("range") if isinstance(pdata.get("applied_range"), dict) else None,
        "source": "sessionhub_9222",
        "download_size": Path(csv_path).stat().st_size if Path(csv_path).exists() else None,
        "context_path": str(context_path),
        "dry_run": False,
    }
    if normalized_month:
        data["month"] = normalized_month
    return CommandResponse(success=True, platform="jst", command="report product-profit export", data=data)
