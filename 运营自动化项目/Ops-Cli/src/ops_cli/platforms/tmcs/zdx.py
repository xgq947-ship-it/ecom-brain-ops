"""猫超智多星（ZDX）货品全站推广计划创建。

页面路径（真实模式）：
天猫超市首页 → 推广 → 推广平台 → 智多星 → 点击前往智多星
→ 货品全站推 → 创建计划 → 填写计划名称、商品ID、每日预算、目标ROI → 确认创建。

本层只负责"执行/预览页面操作"并输出统一 JSON，不做业务判断。
dry-run 不访问页面，不点击确认创建，返回 simulated=True 的结构化预览。
只有 execute=True 时才执行确认创建动作。
"""

from __future__ import annotations

from ops_cli.output import CommandResponse
from ops_cli.platforms.tmcs.shared import TMCS_SITE
from ops_cli.runtime_context import write_runtime_context


ZDX_FULLSITE_PLAN_CREATE_SCENE = "zdx_fullsite_plan_create"

_LEARN_NOTE = (
    "智多星货品全站推广计划创建通过 SessionHub 9222 + Playwright 执行页面操作。"
    "真实路径（@chrome 主力浏览器已探测确认）："
    "天猫超市首页 → 推广 → 推广平台 → 智多星 → 点击前往智多星(vendor_zdx_home) → "
    "底部「货品全站推」tab → 创建计划 → 弹框「创建推广」：填计划名 → 商品ID搜索回车 → 勾选商品 → "
    "右侧「周期日预算」铅笔 → 编辑子框：每日预算→金额→目标投产比「自定义」→ROI→确定 → 「创建完成」。"
    "9222 专用浏览器需已登录猫超；选择器若随页面改版漂移，可重新用 @chrome 探测校准。"
)


def run_zdx_fullsite_plan_create(
    *,
    item_id: str,
    plan_name: str,
    daily_budget: float,
    target_roi: float,
    execute: bool,
    dry_run: bool,
) -> CommandResponse:
    inputs = {
        "item_id": item_id,
        "plan_name": plan_name,
        "daily_budget": daily_budget,
        "target_roi": target_roi,
        "execute": execute,
        "dry_run": dry_run,
    }
    scene = f"{TMCS_SITE}/{ZDX_FULLSITE_PLAN_CREATE_SCENE}"

    if dry_run or not execute:
        context_path = write_runtime_context(
            task_name="tmcs_zdx_fullsite_plan_create",
            status="success",
            inputs=inputs,
            outputs={
                "simulated": True,
                "executed": False,
                "created": False,
                "plan_name": plan_name,
                "item_id": item_id,
                "daily_budget": daily_budget,
                "target_roi": target_roi,
            },
        )
        return CommandResponse(
            success=True,
            platform="tmcs",
            command="zdx fullsite-plan create",
            data={
                "item_id": item_id,
                "plan_name": plan_name,
                "daily_budget": daily_budget,
                "target_roi": target_roi,
                "executed": False,
                "dry_run": dry_run,
                "created": False,
                "simulated": True,
                "source": "simulated",
                "scene": scene,
                "artifacts": [],
                "context_path": str(context_path),
            },
        )

    # 真实执行路径：通过 SessionHub 9222 + Playwright 操作页面
    created, platform_plan_id = _execute_create_plan(
        item_id=item_id,
        plan_name=plan_name,
        daily_budget=daily_budget,
        target_roi=target_roi,
    )

    context_path = write_runtime_context(
        task_name="tmcs_zdx_fullsite_plan_create",
        status="success",
        inputs=inputs,
        outputs={
            "simulated": False,
            "executed": True,
            "created": created,
            "plan_name": plan_name,
            "item_id": item_id,
            "daily_budget": daily_budget,
            "target_roi": target_roi,
            "platform_plan_id": platform_plan_id,
        },
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="zdx fullsite-plan create",
        data={
            "item_id": item_id,
            "plan_name": plan_name,
            "daily_budget": daily_budget,
            "target_roi": target_roi,
            "executed": True,
            "dry_run": False,
            "created": created,
            "simulated": False,
            "platform_plan_id": platform_plan_id,
            "source": "page",
            "scene": scene,
            "artifacts": [],
            "context_path": str(context_path),
        },
    )


def _execute_create_plan(
    *,
    item_id: str,
    plan_name: str,
    daily_budget: float,
    target_roi: float,
) -> tuple[bool, str | None]:
    """通过 SessionHub 9222 + Playwright 执行智多星货品全站推创建计划页面操作。"""
    import sys
    from pathlib import Path

    from ops_cli.config import get_config

    root = Path(get_config().sessionhub_root).expanduser().resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from scene.chrome_cdp import CDP_URL, bring_chrome_to_front, start_chrome  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"无法加载 SessionHub Chrome 依赖：{exc}") from exc

    ok, msg = start_chrome()
    if not ok:
        raise RuntimeError(msg)

    try:
        from playwright.sync_api import Error as PlaywrightError  # type: ignore
        from playwright.sync_api import sync_playwright  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 Playwright，请先运行：pip install -r requirements.txt") from exc

    # 智多星首页 URL（@chrome 主力浏览器探测确认的真实地址）。
    # 路径：天猫超市首页 → 推广 → 推广平台 → 智多星 → 点击前往智多星，落到 vendor_zdx_home。
    ZDX_HOME_URL = (
        "https://web.txcs.tmall.com/?frameUrl="
        "https%3A%2F%2Fweb.txcs.tmall.com%2Fpages%2Fchaoshi%2Fvendor_zdx_home"
    )

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
            page.goto(ZDX_HOME_URL, wait_until="domcontentloaded", timeout=30000)

            if "login" in page.url.lower():
                bring_chrome_to_front()
                raise RuntimeError(
                    "TMCS_LOGIN_REQUIRED：检测到猫超登录页，已切到前台，请先完成登录后重试。"
                )

            import re as _re

            def _find_frame(*required: str, attempts: int = 8, step_ms: int = 1500):
                """查找 body 文本同时包含全部 required 关键词的 iframe（含跨域子帧）。"""
                for _ in range(attempts):
                    for frame in page.frames:
                        try:
                            blob = frame.locator("body").inner_text(timeout=2000)
                        except PlaywrightError:
                            continue
                        if all(kw in blob for kw in required):
                            return frame
                    page.wait_for_timeout(step_ms)
                return None

            # 智多星「货品全站推」列表帧（初始"全部"tab 下尚无"创建计划"按钮，故只认"货品全站推"）。
            list_frame = _find_frame("货品全站推")
            if list_frame is None:
                raise RuntimeError(
                    "SCENE_CAPTURE_FAILED：未找到智多星货品全站推页面，"
                    "请先手动打开该页面后重试，或运行 `ops tmcs zdx fullsite-plan learn`。"
                )

            # 1) 切到「货品全站推」推广模式 tab，等待「创建计划」按钮出现。
            list_frame.get_by_text("货品全站推", exact=True).first.click(timeout=10000)
            page.wait_for_timeout(1500)
            create_btn = list_frame.get_by_role("button", name="创建计划").first
            create_btn.wait_for(state="visible", timeout=10000)

            # 2) 点击「创建计划」，弹框（标题「创建推广」）渲染在更深一层的子 iframe。
            create_btn.click(timeout=10000)
            page.wait_for_timeout(2500)

            # 3) 定位弹框所在帧：含「计划名称」+「创建完成」，与列表帧不同（嵌套子帧）。
            modal = _find_frame("计划名称", "创建完成", "投放商品")
            if modal is None:
                raise RuntimeError(
                    "PLAN_CREATE_FAILED：未找到「创建推广」弹框帧（计划名称/创建完成）。"
                )

            # 计划名 / 商品搜索：弹框内仅这两个占位符为「请输入」的文本框，按 DOM 顺序取。
            req_inputs = modal.get_by_placeholder("请输入")
            name_input = req_inputs.nth(0)
            item_input = req_inputs.nth(1)

            # 4) 计划名称（默认值"货品全站推_<时间戳>"，fill 先清空再填）。
            name_input.fill(plan_name, timeout=8000)

            # 5) 商品ID 搜索 → 回车筛选（真实键盘输入，兼容受控组件）。
            item_input.click(timeout=8000)
            item_input.fill("", timeout=8000)
            item_input.press_sequentially(item_id, delay=30)
            item_input.press("Enter")
            page.wait_for_timeout(2500)

            # 6) 校验目标商品ID 在列表出现（该 ID 全局唯一，11 条 catalog 中仅 1 行含此 ID）。
            modal_text = modal.locator("body").inner_text(timeout=5000)
            if item_id not in modal_text:
                raise RuntimeError(
                    f"PRODUCT_NOT_FOUND：搜索商品ID {item_id!r} 后未在列表命中该商品，请确认商品ID。"
                )

            # 7) 按含 item_id 文本的唯一行定位行内复选框，force 点击（next-design 隐藏 input
            #    被样式 span 覆盖，普通点击不切换状态，force 点击命中覆盖层处理器）。
            rows = modal.get_by_role("row").filter(has_text=item_id)
            if rows.count() == 0:
                raise RuntimeError(
                    f"PRODUCT_NOT_FOUND：未能按商品ID {item_id!r} 定位到商品行。"
                )
            cb = rows.first.get_by_role("checkbox").first
            if cb.count() == 0:
                raise RuntimeError(
                    f"PRODUCT_NOT_FOUND：商品ID {item_id!r} 所在行未找到复选框。"
                )
            cb.click(force=True, timeout=5000)
            page.wait_for_timeout(1200)

            # 勾选后右侧「已选择」应由 0/1 变 1/1；以 0/1 消失为准（容忍空格/全角差异）。
            after_text = modal.locator("body").inner_text(timeout=5000).replace(" ", "")
            if "1/1" not in after_text and "0/1" in after_text:
                raise RuntimeError(
                    f"PRODUCT_NOT_FOUND：勾选商品ID {item_id!r} 后未确认到「已选择 1/1」，已中止。"
                )

            # 8) 打开右侧「周期日预算」编辑铅笔（vc-image，class 为 hash，用中文标签锚定后第1个 img）。
            modal.locator(
                "xpath=//*[contains(text(),'周期日预算')]/following::img[1]"
            ).first.click(timeout=8000)
            page.wait_for_timeout(1500)

            # 编辑子框与主弹框同帧，继续在 modal 内操作。
            # 9) 预算类型选「每日预算」。
            modal.get_by_text("每日预算", exact=True).first.click(timeout=8000)
            page.wait_for_timeout(800)

            # 10) 填每日预算金额。编辑框打开后，占位符「请输入」的文本框依次为
            #     计划名(0)、商品搜索(1)、每日预算(2)（DOM 顺序，主力 Chrome 已确认）。
            budget_input = modal.get_by_placeholder("请输入").nth(2)
            budget_input.fill(str(int(daily_budget)), timeout=8000)
            page.wait_for_timeout(500)

            # 11) 目标投产比选「自定义」，ROI 输入框占位符为「请输入数字」（唯一稳定）。
            modal.get_by_text("自定义", exact=True).first.click(timeout=8000)
            page.wait_for_timeout(800)
            modal.get_by_placeholder("请输入数字").first.fill(f"{target_roi:.2f}", timeout=8000)
            page.wait_for_timeout(500)

            # 12) 子框「确定」，把预算/ROI 暂存回主弹框。
            modal.get_by_role("button", name="确定").first.click(timeout=8000)
            page.wait_for_timeout(1500)

            # 13) 提交前最终校验：计划名 + 商品 + 暂存的预算/ROI 全部一致，才点「创建完成」。
            staged_name = name_input.input_value(timeout=5000)
            if staged_name != plan_name:
                raise RuntimeError(
                    f"PLAN_CREATE_FAILED：提交前计划名校验失败，期望 {plan_name!r}，实际 {staged_name!r}。"
                )
            final_text = modal.locator("body").inner_text(timeout=5000)
            if item_id not in final_text:
                raise RuntimeError(
                    "PLAN_CREATE_FAILED：提交前未确认到已选中目标商品，已中止，未点击「创建完成」。"
                )
            # 右侧面板回显形如「每日预算: 100」「控投产比投放 5.07」，逐项核对防误填后误提交。
            budget_str = str(int(daily_budget))
            roi_str = f"{target_roi:.2f}"
            if budget_str not in final_text or roi_str not in final_text:
                raise RuntimeError(
                    f"PLAN_CREATE_FAILED：提交前预算/ROI 暂存校验失败"
                    f"（期望每日预算 {budget_str}、ROI {roi_str} 出现在面板回显）；"
                    "为防误创建，已中止，未点击「创建完成」。"
                )

            # 14) 点击「创建完成」真实提交（只有 execute=True 才会执行到此）。
            modal.get_by_role("button", name="创建完成").first.click(timeout=10000)
            page.wait_for_timeout(3000)

            # 尝试从计划列表读取本次新计划ID：定位「计划名称」列含 plan_name 的行，取其计划ID。
            # 取不到就置空，绝不返回页面上其它商品/计划的无关数字，避免误导。
            platform_plan_id: str | None = None
            try:
                plan_row = list_frame.get_by_role("row").filter(has_text=plan_name).first
                if plan_row.count() >= 1:
                    id_match = _re.search(r"\b(\d{9,})\b", plan_row.inner_text(timeout=3000))
                    if id_match:
                        platform_plan_id = id_match.group(1)
            except PlaywrightError:
                pass

            return True, platform_plan_id

        except RuntimeError:
            raise
        except PlaywrightError as exc:
            raise RuntimeError(f"PLATFORM_REQUEST_FAILED：页面操作失败：{exc}") from exc
        finally:
            if created_page:
                try:
                    page.close()
                except Exception:
                    pass


def learn_zdx_fullsite_plan_create(*, force: bool = False) -> CommandResponse:
    inputs = {
        "site": TMCS_SITE,
        "scene": ZDX_FULLSITE_PLAN_CREATE_SCENE,
        "force": force,
    }
    context_path = write_runtime_context(
        task_name="tmcs_zdx_fullsite_plan_learn",
        status="success",
        inputs=inputs,
        outputs={
            "site": TMCS_SITE,
            "scene": ZDX_FULLSITE_PLAN_CREATE_SCENE,
            "note": _LEARN_NOTE,
        },
    )
    return CommandResponse(
        success=True,
        platform="tmcs",
        command="zdx fullsite-plan learn",
        data={
            "site": TMCS_SITE,
            "scene": ZDX_FULLSITE_PLAN_CREATE_SCENE,
            "note": _LEARN_NOTE,
            "next_command": "ops --json tmcs zdx fullsite-plan create --item-id <商品ID> --plan-name <计划名称> --daily-budget <金额> --target-roi <ROI> --execute",
            "context_path": str(context_path),
        },
    )
