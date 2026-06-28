"""JST platform registration — commands and capabilities."""
from __future__ import annotations

import typer

from ops_cli.capabilities import CapabilitySpec
from ops_cli.cli_helpers import _execute
from ops_cli.output import CommandResponse
from ops_cli.platforms.jst.auth import check_auth, capture_auth, ensure_auth
from ops_cli.platforms.jst.browser import learn_jst_browser_scene
from ops_cli.platforms.jst.invoice import (
    DEFAULT_INVOICE_TYPE,
    DEFAULT_QUANTITY,
    learn_order_invoice_workorder,
    run_order_invoice_workorder,
)
from ops_cli.platforms.jst.order import (
    DEFAULT_LABELS,
    DEFAULT_REMARK_TEXT,
    learn_order_logistics,
    run_order_label,
    run_order_logistics,
    run_order_normalize,
    run_order_query,
    run_order_remark,
)
from ops_cli.platforms.jst.exchange_resend import (
    learn_order_exchange_resend,
    preview_order_exchange_resend,
    submit_order_exchange_resend,
)
from ops_cli.platforms.jst.pickup_watch import run_pickup_watch
from ops_cli.platforms.jst.sms_verification import detect_sms_dialog, mask_code, submit_sms_code
from ops_cli.platforms.jst.profit import get_month_profit, learn_jst_profit_scene, run_day_profit, run_yesterday_profit
from ops_cli.platforms.jst.product import learn_jst_product_sync, run_product_sync
from ops_cli.platforms.jst.report import DEFAULT_SHOP_NAME, export_product_profit_csv, probe_goods_profit_export
from ops_cli.platforms.jst.reimburse import run_order_reimburse_workorder
from ops_cli.platforms.jst.shop_goods import import_jst_shop_goods
from ops_cli.platforms.jst.shops import resolve_shop
from ops_cli.platforms.jst.stats import learn_order_stats, run_order_stats

_SHOP_OPT_HELP = "店铺选择：注册表 key/shop_id/店铺名，留空=默认店铺。"


def register(app: typer.Typer, capabilities: dict[str, CapabilitySpec]) -> None:
    jst_app = typer.Typer(help="Jushuitan platform commands.", no_args_is_help=True)
    jst_auth_app = typer.Typer(help="JST auth commands.", no_args_is_help=True)
    jst_auth_sms_app = typer.Typer(help="JST SMS verification commands.", no_args_is_help=True)
    jst_profit_app = typer.Typer(help="JST profit commands.", no_args_is_help=True)
    jst_report_app = typer.Typer(help="JST report commands.", no_args_is_help=True)
    jst_report_product_profit_app = typer.Typer(help="JST product profit report commands.", no_args_is_help=True)
    jst_product_app = typer.Typer(help="JST product commands.", no_args_is_help=True)
    jst_browser_app = typer.Typer(help="JST browser learning commands.", no_args_is_help=True)
    jst_shop_goods_app = typer.Typer(help="JST shop goods import commands.", no_args_is_help=True)
    jst_order_app = typer.Typer(help="JST order commands.", no_args_is_help=True)
    jst_order_invoice_app = typer.Typer(help="JST order invoice workorder commands.")
    jst_order_logistics_app = typer.Typer(help="JST order logistics commands.")
    jst_order_reimburse_app = typer.Typer(help="JST brush reimburse workorder commands.")
    jst_order_exchange_resend_app = typer.Typer(help="JST order exchange / resend commands.", no_args_is_help=True)
    jst_order_stats_app = typer.Typer(help="JST order stats commands.")

    # --- Auth ---

    @jst_auth_app.command("check")
    def jst_auth_check(ctx: typer.Context) -> None:
        _execute(ctx, command_name="ops jst auth check", params={}, handler=check_auth)

    @jst_auth_app.command("ensure")
    def jst_auth_ensure(ctx: typer.Context) -> None:
        _execute(ctx, command_name="ops jst auth ensure", params={}, handler=ensure_auth)

    @jst_auth_app.command("capture")
    def jst_auth_capture(ctx: typer.Context) -> None:
        _execute(ctx, command_name="ops jst auth capture", params={}, handler=capture_auth)

    # --- Auth SMS verification（9222 短信验证码弹窗检测/提交）---

    @jst_auth_sms_app.command("detect")
    def jst_auth_sms_detect(
        ctx: typer.Context,
        screenshot_dir: str | None = typer.Option(None, "--screenshot-dir", help="检测截图保存目录（文件名不含验证码）。"),
        dry_run: bool = typer.Option(False, "--dry-run", help="只读检测，不填写、不提交。"),
        output: str = typer.Option("json", "--output", help="Output format. Currently only json is supported."),
    ) -> None:
        if output.lower() != "json":
            raise typer.BadParameter("当前仅支持 --output json。")
        _execute(
            ctx,
            command_name="ops jst auth sms detect",
            params={"screenshot_dir": screenshot_dir, "dry_run": dry_run},
            handler=lambda: detect_sms_dialog(screenshot_dir=screenshot_dir, dry_run=dry_run),
            force_json=True,
        )

    @jst_auth_sms_app.command("submit")
    def jst_auth_sms_submit(
        ctx: typer.Context,
        code: str = typer.Option(..., "--code", help="用户主动提供的 4 位短信验证码。"),
        execute: bool = typer.Option(False, "--execute", help="真正填写并提交（缺省只校验，不写入）。"),
        screenshot_dir: str | None = typer.Option(None, "--screenshot-dir", help="检测截图保存目录（文件名不含验证码）。"),
        output: str = typer.Option("json", "--output", help="Output format. Currently only json is supported."),
    ) -> None:
        if output.lower() != "json":
            raise typer.BadParameter("当前仅支持 --output json。")
        # 注意：params 落入 Ops-Cli 命令日志，这里只记 masked_code，绝不记明文。
        _execute(
            ctx,
            command_name="ops jst auth sms submit",
            params={"masked_code": mask_code(code), "execute": execute, "screenshot_dir": screenshot_dir},
            handler=lambda: submit_sms_code(code=code, execute=execute, screenshot_dir=screenshot_dir),
            force_json=True,
        )

    # --- Profit ---

    @jst_profit_app.command("yesterday")
    def jst_profit_yesterday(
        ctx: typer.Context,
        shop: str | None = typer.Option(None, "--shop", help=_SHOP_OPT_HELP),
        detail: bool = typer.Option(False, "--detail", help="Include all profit metric rows and raw response data."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst profit yesterday",
            params={"shop": shop, "detail": detail},
            handler=lambda: run_yesterday_profit(shop=shop, detail=detail),
        )

    @jst_profit_app.command("day")
    def jst_profit_day(
        ctx: typer.Context,
        date_value: str = typer.Option(..., "--date", help="Target day: YYYY-MM-DD / today / yesterday."),
        shop: str | None = typer.Option(None, "--shop", help=_SHOP_OPT_HELP),
        detail: bool = typer.Option(False, "--detail", help="Include all profit metric rows and raw response data."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst profit day",
            params={"date": date_value, "shop": shop, "detail": detail},
            handler=lambda: run_day_profit(date_value=date_value, shop=shop, detail=detail),
        )

    @jst_profit_app.command("learn")
    def jst_profit_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst profit learn",
            params={"force": force},
            handler=lambda: learn_jst_profit_scene(force=force),
        )

    @jst_profit_app.command("month")
    def jst_profit_month(
        ctx: typer.Context,
        month: str = typer.Option(..., "--month", help="Target month in YYYY-MM."),
        shop: str | None = typer.Option(None, "--shop", help=_SHOP_OPT_HELP),
        detail: bool = typer.Option(False, "--detail", help="Include all profit metric rows and raw response data."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst profit month",
            params={"month": month, "shop": shop, "detail": detail},
            handler=lambda: get_month_profit(month=month, shop=shop, detail=detail),
        )

    # --- Report ---

    @jst_report_product_profit_app.command("export")
    def jst_report_product_profit_export(
        ctx: typer.Context,
        shop: str | None = typer.Option(None, "--shop", help=_SHOP_OPT_HELP),
        shop_name: str = typer.Option(DEFAULT_SHOP_NAME, "--shop-name", help="JST shop name（--shop 优先）。"),
        month: str | None = typer.Option(None, "--month", help="Target month YYYY-MM. Default: last month."),
        start_date: str | None = typer.Option(None, "--start-date", help="Start date YYYY-MM-DD. Use with --end-date."),
        end_date: str | None = typer.Option(None, "--end-date", help="End date YYYY-MM-DD. Use with --start-date."),
        dest: str | None = typer.Option(None, "--dest", help="Destination CSV path or directory."),
        download_dir: str | None = typer.Option(None, "--download-dir", help="Override browser download directory."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, never trigger a real export."),
        execute: bool = typer.Option(False, "--execute", help="Actually collect the exported CSV."),
        output: str = typer.Option("json", "--output", help="Output format. Currently only json is supported."),
    ) -> None:
        if output.lower() != "json":
            raise typer.BadParameter("当前仅支持 --output json。")
        if shop:
            shop_name = resolve_shop(shop).shop_name
        effective_dry_run = dry_run or not execute
        _execute(
            ctx,
            command_name="ops jst report product-profit export",
            params={
                "shop_name": shop_name,
                "month": month,
                "start_date": start_date,
                "end_date": end_date,
                "dest": dest,
                "download_dir": download_dir,
                "dry_run": effective_dry_run,
            },
            handler=lambda: export_product_profit_csv(
                shop_name=shop_name,
                month=month,
                start_date=start_date,
                end_date=end_date,
                dry_run=effective_dry_run,
                dest=dest,
                download_dir=download_dir,
            ),
            force_json=True,
        )

    @jst_report_product_profit_app.command("learn")
    def jst_report_product_profit_learn(
        ctx: typer.Context,
        shop: str | None = typer.Option(None, "--shop", help=_SHOP_OPT_HELP),
        shop_name: str = typer.Option(DEFAULT_SHOP_NAME, "--shop-name", help="JST shop name（--shop 优先）。"),
        month: str | None = typer.Option(None, "--month", help="Target month YYYY-MM. Default: last month."),
        start_date: str | None = typer.Option(None, "--start-date", help="Start date YYYY-MM-DD. Use with --end-date."),
        end_date: str | None = typer.Option(None, "--end-date", help="End date YYYY-MM-DD. Use with --start-date."),
        dest: str | None = typer.Option(None, "--dest", help="Destination CSV path for the captured download."),
    ) -> None:
        if shop:
            shop_name = resolve_shop(shop).shop_name
        _execute(
            ctx,
            command_name="ops jst report product-profit learn",
            params={"shop_name": shop_name, "month": month, "start_date": start_date, "end_date": end_date, "dest": dest},
            handler=lambda: probe_goods_profit_export(
                shop_name=shop_name, month=month, start_date=start_date, end_date=end_date, dest=dest
            ),
            force_json=True,
        )

    # --- Product ---

    @jst_product_app.command("sync", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    def jst_product_sync(
        ctx: typer.Context,
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, do not download or overwrite."),
        use_local_only: bool = typer.Option(False, "--use-local-only", help="Skip backend export and use the local Downloads file."),
        keep_brands: str | None = typer.Option(None, "--keep-brands", help="Brands to keep. Accepts one or more values."),
    ) -> None:
        keep_brand_values = [keep_brands] if keep_brands else []
        keep_brand_values.extend(arg for arg in ctx.args if not str(arg).startswith("-"))
        _execute(
            ctx,
            command_name="ops jst product sync",
            params={"dry_run": dry_run, "use_local_only": use_local_only, "keep_brands": keep_brand_values},
            handler=lambda: run_product_sync(
                dry_run=dry_run,
                use_local_only=use_local_only,
                keep_brands=keep_brand_values or None,
            ),
        )

    @jst_product_app.command("learn")
    def jst_product_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst product learn",
            params={"force": force},
            handler=lambda: learn_jst_product_sync(force=force),
        )

    # --- Browser ---

    @jst_browser_app.command("learn")
    def jst_browser_learn(
        ctx: typer.Context,
        scene: str = typer.Option(..., "--scene", help="JST browser scene name, for example shop-goods-import."),
        timeout: int = typer.Option(90, "--timeout", help="Seconds to wait while user manually completes the page flow in primary Chrome."),
        cdp_url: str | None = typer.Option(None, "--cdp-url", help="Primary Chrome CDP URL. Do not use 9222 here."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst browser learn",
            params={"scene": scene, "timeout": timeout, "cdp_url": cdp_url},
            handler=lambda: learn_jst_browser_scene(scene=scene, timeout=timeout, cdp_url=cdp_url),
        )

    # --- Shop Goods ---

    @jst_shop_goods_app.command("import")
    def jst_shop_goods_import(
        ctx: typer.Context,
        file_path: str = typer.Option(..., "--file", help="Path to JST shop goods import xlsx."),
        shop: str | None = typer.Option(None, "--shop", help=_SHOP_OPT_HELP),
        shop_name: str = typer.Option("（猫超）启明工贸有限公司", "--shop-name", help="JST shop name（--shop 优先）。"),
        mode: str = typer.Option("ignore", "--mode", help="Import mode: ignore or cover."),
        output: str = typer.Option("json", "--output", help="Output format. Currently only json is supported."),
    ) -> None:
        if output.lower() != "json":
            raise typer.BadParameter("当前仅支持 --output json。")
        if shop:
            shop_name = resolve_shop(shop).shop_name
        _execute(
            ctx,
            command_name="ops jst shop-goods import",
            params={"file": file_path, "shop_name": shop_name, "mode": mode, "output": output},
            handler=lambda: import_jst_shop_goods(file_path=file_path, shop_name=shop_name, mode=mode),
        )

    # --- Order ---

    @jst_order_app.command("query")
    def jst_order_query(
        ctx: typer.Context,
        date_value: str = typer.Option("today", "--date", help="Target date: today or YYYY-MM-DD."),
        order_id: list[str] = typer.Option(None, "--order-id", help="Order ID. Repeatable."),
        shop_name: str | None = typer.Option(None, "--shop-name", help="JST shop name filter."),
        status: str | None = typer.Option(None, "--status", help="JST order status filter."),
        keyword: str | None = typer.Option(None, "--keyword", help="Product keyword filter."),
        limit: int | None = typer.Option(None, "--limit", help="Only keep the first N matched orders."),
        output: str = typer.Option("json", "--output", help="Output format. Currently only json is supported."),
    ) -> None:
        if output.lower() != "json":
            raise typer.BadParameter("当前仅支持 --output json。")
        _execute(
            ctx,
            command_name="ops jst order query",
            params={
                "date": date_value,
                "order_ids": order_id,
                "shop_name": shop_name,
                "status": status,
                "keyword": keyword,
                "limit": limit,
                "output": output,
            },
            handler=lambda: run_order_query(
                date_value=date_value,
                order_ids=order_id,
                shop_name=shop_name,
                status=status,
                keyword=keyword,
                limit=limit,
            ),
            force_json=True,
        )

    @jst_order_app.command("label")
    def jst_order_label(
        ctx: typer.Context,
        order_id: list[str] = typer.Option(None, "--order-id", help="Order ID. Repeatable."),
        input_path: str | None = typer.Option(None, "--input", help="Order JSON file with orders[]."),
        limit: int | None = typer.Option(None, "--limit", help="Only process the first N orders."),
        execute: bool = typer.Option(False, "--execute", help="Actually write remark and labels."),
        label: str = typer.Option(DEFAULT_LABELS, "--label", help="Labels text."),
        remark_text: str = typer.Option(DEFAULT_REMARK_TEXT, "--remark-text", help="Remark text."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order label",
            params={
                "order_ids": order_id,
                "input_path": input_path,
                "limit": limit,
                "execute": execute,
                "labels": label,
                "remark_text": remark_text,
            },
            handler=lambda: run_order_label(
                order_ids=order_id,
                input_path=input_path,
                limit=limit,
                execute=execute,
                labels=label,
                remark_text=remark_text,
            ),
        )

    @jst_order_app.command("remark")
    def jst_order_remark(
        ctx: typer.Context,
        order_id: list[str] = typer.Option(None, "--order-id", help="Order ID. Repeatable."),
        input_path: str | None = typer.Option(None, "--input", help="Order JSON file with orders[]."),
        limit: int | None = typer.Option(None, "--limit", help="Only process the first N orders."),
        execute: bool = typer.Option(False, "--execute", help="Actually write seller remark."),
        remark_text: str = typer.Option(..., "--remark-text", help="Seller remark text."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order remark",
            params={
                "order_ids": order_id,
                "input_path": input_path,
                "limit": limit,
                "execute": execute,
                "remark_text": remark_text,
            },
            handler=lambda: run_order_remark(
                order_ids=order_id,
                input_path=input_path,
                limit=limit,
                execute=execute,
                remark_text=remark_text,
            ),
        )

    @jst_order_app.command("normalize")
    def jst_order_normalize(
        ctx: typer.Context,
        order_id: list[str] = typer.Option(None, "--order-id", help="Order ID. Repeatable."),
        input_path: str | None = typer.Option(None, "--input", help="Order JSON file with orders[]."),
        limit: int | None = typer.Option(None, "--limit", help="Only process the first N orders."),
        execute: bool = typer.Option(False, "--execute", help="Actually convert abnormal order to normal (转正常单)."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order normalize",
            params={
                "order_ids": order_id,
                "input_path": input_path,
                "limit": limit,
                "execute": execute,
            },
            handler=lambda: run_order_normalize(
                order_ids=order_id,
                input_path=input_path,
                limit=limit,
                execute=execute,
            ),
        )

    @jst_order_app.command("pickup-watch")
    def jst_order_pickup_watch(
        ctx: typer.Context,
        hours: int = typer.Option(48, "--hours", help="Lookback hours for paid orders."),
        shop_name: str | None = typer.Option(None, "--shop-name", help="Optional JST shop name filter."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Use representative local sample orders only."),
        debug: bool = typer.Option(False, "--debug", help="Include platform debugging diagnostics when supported."),
        output: str = typer.Option("json", "--output", help="Output format. Currently only json is supported."),
    ) -> None:
        if output.lower() != "json":
            raise typer.BadParameter("当前仅支持 --output json。")
        _execute(
            ctx,
            command_name="ops jst order pickup-watch",
            params={
                "hours": hours,
                "shop_name": shop_name,
                "dry_run": dry_run,
                "debug": debug,
                "output": output,
            },
            handler=lambda: run_pickup_watch(hours=hours, shop_name=shop_name, dry_run=dry_run, debug=debug),
            force_json=True,
        )

    # --- Order Logistics ---

    @jst_order_logistics_app.callback(invoke_without_command=True)
    def jst_order_logistics(
        ctx: typer.Context,
        order_id: list[str] = typer.Option(None, "--order-id", help="JST order number. Repeatable."),
        outer_order_id: list[str] = typer.Option(None, "--outer-order-id", help="External platform order number. Repeatable."),
        input_path: str | None = typer.Option(None, "--input", help="Order input file. Supports JSON/TXT/CSV."),
        limit: int | None = typer.Option(None, "--limit", help="Only query the first N orders."),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        _execute(
            ctx,
            command_name="ops jst order logistics",
            params={
                "order_ids": order_id,
                "outer_order_ids": outer_order_id,
                "input_path": input_path,
                "limit": limit,
            },
            handler=lambda: run_order_logistics(
                order_ids=order_id,
                outer_order_ids=outer_order_id,
                input_path=input_path,
                limit=limit,
            ),
        )

    @jst_order_logistics_app.command("learn")
    def jst_order_logistics_learn(
        ctx: typer.Context,
        order_id: str | None = typer.Option(None, "--order-id", help="JST order number used to trigger logistics panel."),
        outer_order_id: str | None = typer.Option(None, "--outer-order-id", help="External order number used to trigger logistics panel."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order logistics learn",
            params={"order_id": order_id, "outer_order_id": outer_order_id},
            handler=lambda: learn_order_logistics(order_id=order_id, outer_order_id=outer_order_id),
        )

    # --- Order Invoice ---

    @jst_order_invoice_app.callback(invoke_without_command=True)
    def jst_order_invoice(
        ctx: typer.Context,
        order_id: str | None = typer.Option(None, "--order-id", help="JST order number or platform order number."),
        outer_order_id: str | None = typer.Option(None, "--outer-order-id", help="External platform order number."),
        invoice_type: str = typer.Option(DEFAULT_INVOICE_TYPE, "--invoice-type", help="Invoice type. Default: 专用发票."),
        shop_name: str | None = typer.Option(None, "--shop-name", help="JST shop name."),
        invoice_entity: str | None = typer.Option(None, "--invoice-entity", help="Invoice entity company name."),
        title: str | None = typer.Option(None, "--title", help="Invoice title."),
        tax_no: str | None = typer.Option(None, "--tax-no", help="Tax number."),
        address: str | None = typer.Option(None, "--address", help="Company address for special VAT invoice."),
        phone: str | None = typer.Option(None, "--phone", help="Company phone for special VAT invoice."),
        bank: str | None = typer.Option(None, "--bank", help="Bank name for special VAT invoice."),
        bank_account: str | None = typer.Option(None, "--bank-account", help="Bank account for special VAT invoice."),
        amount: str | None = typer.Option(None, "--amount", help="Invoice amount."),
        quantity: int = typer.Option(DEFAULT_QUANTITY, "--quantity", help="Product quantity."),
        execute: bool = typer.Option(False, "--execute", help="Actually create invoice workorder."),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        _execute(
            ctx,
            command_name="ops jst order invoice",
            params={
                "order_id": order_id,
                "outer_order_id": outer_order_id,
                "invoice_type": invoice_type,
                "shop_name": shop_name,
                "invoice_entity": invoice_entity,
                "title": title,
                "tax_no": tax_no,
                "address": address,
                "phone": phone,
                "bank": bank,
                "bank_account": bank_account,
                "amount": amount,
                "quantity": quantity,
                "execute": execute,
            },
            handler=lambda: run_order_invoice_workorder(
                order_id=order_id or "",
                outer_order_id=outer_order_id or "",
                invoice_type=invoice_type,
                shop_name=shop_name or "",
                invoice_entity=invoice_entity or "",
                title=title or "",
                tax_no=tax_no or "",
                address=address or "",
                phone=phone or "",
                bank=bank or "",
                bank_account=bank_account or "",
                amount=amount or "",
                quantity=quantity,
                execute=execute,
            ),
        )

    @jst_order_invoice_app.command("learn")
    def jst_order_invoice_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order invoice learn",
            params={"force": force},
            handler=lambda: learn_order_invoice_workorder(force=force),
        )

    # --- Order Reimburse ---

    @jst_order_reimburse_app.callback(invoke_without_command=True)
    def jst_order_reimburse(
        ctx: typer.Context,
        outer_order_id: str = typer.Option(..., "--outer-order-id", help="External platform order number."),
        principal_total: str = typer.Option(..., "--principal-total", help="Brush order principal total."),
        payout_total: str = typer.Option(..., "--payout-total", help="Brush commission payout total."),
        product_code: str = typer.Option(..., "--product-code", help="Product code for workorder field."),
        product_name: str = typer.Option("", "--product-name", help="Product name fallback for workorder field."),
        workbook_file: str = typer.Option("", "--workbook-file", help="Workbook file to upload when executing."),
        execute: bool = typer.Option(False, "--execute", help="Upload workbook and create reimburse workorder."),
        shop: str | None = typer.Option(None, "--shop", help=_SHOP_OPT_HELP),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        _execute(
            ctx,
            command_name="ops jst order reimburse",
            params={
                "outer_order_id": outer_order_id,
                "principal_total": principal_total,
                "payout_total": payout_total,
                "product_code": product_code,
                "product_name": product_name,
                "workbook_file": workbook_file,
                "execute": execute,
                "shop": shop,
            },
            handler=lambda: run_order_reimburse_workorder(
                outer_order_id=outer_order_id,
                principal_total=principal_total,
                payout_total=payout_total,
                product_code=product_code,
                product_name=product_name,
                workbook_file=workbook_file,
                execute=execute,
                shop=shop,
            ),
        )

    # --- Order Exchange / Resend ---

    @jst_order_exchange_resend_app.command("learn")
    def jst_order_exchange_resend_learn(
        ctx: typer.Context,
        order_no: str = typer.Option(..., "--order-no", help="JST order number / online order number."),
        mode: str = typer.Option(..., "--mode", help="resend（补发）or exchange（换货）。"),
        reason: str | None = typer.Option(None, "--reason", help="Optional reason."),
        remark: str | None = typer.Option(None, "--remark", help="Optional remark."),
        sku_code: str | None = typer.Option(None, "--sku-code", help="Optional target SKU code."),
        qty: int = typer.Option(1, "--qty", help="Quantity, default 1."),
        screenshot_dir: str | None = typer.Option(None, "--screenshot-dir", help="Directory to save exploration screenshots."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Learn only explores; never submits regardless."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order exchange-resend learn",
            params={
                "order_no": order_no,
                "mode": mode,
                "reason": reason,
                "remark": remark,
                "sku_code": sku_code,
                "qty": qty,
                "screenshot_dir": screenshot_dir,
                "dry_run": dry_run,
            },
            handler=lambda: learn_order_exchange_resend(
                order_no=order_no,
                mode=mode,
                reason=reason,
                remark=remark,
                sku_code=sku_code,
                qty=qty,
                screenshot_dir=screenshot_dir,
            ),
        )

    @jst_order_exchange_resend_app.command("preview")
    def jst_order_exchange_resend_preview(
        ctx: typer.Context,
        order_no: str = typer.Option(..., "--order-no", help="JST order number / online order number."),
        mode: str = typer.Option(..., "--mode", help="resend（补发）or exchange（换货）。"),
        reason: str | None = typer.Option(None, "--reason", help="Optional reason."),
        remark: str | None = typer.Option(None, "--remark", help="Optional remark."),
        sku_code: str | None = typer.Option(None, "--sku-code", help="Optional target SKU code."),
        qty: int = typer.Option(1, "--qty", help="Quantity, default 1."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only; never submits."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order exchange-resend preview",
            params={
                "order_no": order_no,
                "mode": mode,
                "reason": reason,
                "remark": remark,
                "sku_code": sku_code,
                "qty": qty,
                "dry_run": dry_run,
            },
            handler=lambda: preview_order_exchange_resend(
                order_no=order_no,
                mode=mode,
                reason=reason,
                remark=remark,
                sku_code=sku_code,
                qty=qty,
            ),
        )

    @jst_order_exchange_resend_app.command("submit")
    def jst_order_exchange_resend_submit(
        ctx: typer.Context,
        order_no: str = typer.Option(..., "--order-no", help="JST order number / online order number."),
        mode: str = typer.Option(..., "--mode", help="resend（补发）or exchange（换货）。"),
        confirm_order_no: str | None = typer.Option(None, "--confirm-order-no", help="Must equal --order-no for real submit."),
        reason: str | None = typer.Option(None, "--reason", help="Optional reason."),
        remark: str | None = typer.Option(None, "--remark", help="Optional remark."),
        sku_code: str | None = typer.Option(None, "--sku-code", help="Optional target SKU code."),
        qty: int = typer.Option(1, "--qty", help="Quantity, default 1."),
        execute: bool = typer.Option(False, "--execute", help="Required to attempt a real submit."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order exchange-resend submit",
            params={
                "order_no": order_no,
                "mode": mode,
                "confirm_order_no": confirm_order_no,
                "reason": reason,
                "remark": remark,
                "sku_code": sku_code,
                "qty": qty,
                "execute": execute,
            },
            handler=lambda: submit_order_exchange_resend(
                order_no=order_no,
                mode=mode,
                confirm_order_no=confirm_order_no,
                reason=reason,
                remark=remark,
                sku_code=sku_code,
                qty=qty,
                execute=execute,
            ),
        )

    # --- Order Stats ---

    @jst_order_stats_app.callback(invoke_without_command=True)
    def jst_order_stats(
        ctx: typer.Context,
        date_value: str = typer.Option("today", "--date", help="today or YYYY-MM-DD."),
        store: str | None = typer.Option(None, "--store", help="Store name override（最高优先级）。"),
        shop: str | None = typer.Option(None, "--shop", help=_SHOP_OPT_HELP),
    ) -> None:
        if ctx.invoked_subcommand is not None:
            return
        _execute(
            ctx,
            command_name="ops jst order stats",
            params={"date": date_value, "store": store, "shop": shop},
            handler=lambda: run_order_stats(date_arg=date_value, store=store, shop=shop),
        )

    @jst_order_stats_app.command("learn")
    def jst_order_stats_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops jst order stats learn",
            params={"force": force},
            handler=lambda: learn_order_stats(force=force),
        )

    # --- Wire up Typer hierarchy ---

    jst_auth_app.add_typer(jst_auth_sms_app, name="sms")
    jst_app.add_typer(jst_auth_app, name="auth")
    jst_app.add_typer(jst_profit_app, name="profit")
    jst_report_app.add_typer(jst_report_product_profit_app, name="product-profit")
    jst_app.add_typer(jst_report_app, name="report")
    jst_app.add_typer(jst_product_app, name="product")
    jst_app.add_typer(jst_browser_app, name="browser")
    jst_app.add_typer(jst_shop_goods_app, name="shop-goods")
    jst_app.add_typer(jst_order_app, name="order")
    jst_order_app.add_typer(jst_order_invoice_app, name="invoice")
    jst_order_app.add_typer(jst_order_logistics_app, name="logistics")
    jst_order_app.add_typer(jst_order_reimburse_app, name="reimburse")
    jst_order_app.add_typer(jst_order_exchange_resend_app, name="exchange-resend")
    jst_order_app.add_typer(jst_order_stats_app, name="stats")

    # --- Register capabilities ---

    for spec in [
        CapabilitySpec(id="jst.auth.check", platform="jst", command="auth check", scenes=("order_list",), recovery_policy="never"),
        CapabilitySpec(id="jst.auth.ensure", platform="jst", command="auth ensure", scenes=("order_list",)),
        CapabilitySpec(id="jst.auth.capture", platform="jst", command="auth capture", scenes=("order_list",), recovery_policy="explicit"),
        CapabilitySpec(id="jst.auth.sms.detect", platform="jst", command="auth sms detect", recovery_policy="never"),
        CapabilitySpec(id="jst.auth.sms.submit", platform="jst", command="auth sms submit", recovery_policy="never"),
        CapabilitySpec(id="jst.profit.yesterday", platform="jst", command="profit yesterday", scenes=("business_profit_multi_dimension_report",)),
        CapabilitySpec(id="jst.profit.day", platform="jst", command="profit day", scenes=("business_profit_multi_dimension_report",)),
        CapabilitySpec(id="jst.profit.learn", platform="jst", command="profit learn", scenes=("business_profit_multi_dimension_report",), recovery_policy="explicit"),
        CapabilitySpec(id="jst.profit.month", platform="jst", command="profit month", scenes=("business_profit_multi_dimension_report",)),
        CapabilitySpec(id="jst.report.product-profit.export", platform="jst", command="report product-profit export", scenes=("business_profit_multi_dimension_report",), artifact_types=("csv",)),
        CapabilitySpec(id="jst.report.product-profit.learn", platform="jst", command="report product-profit learn", scenes=("goods_profit_export",), recovery_policy="explicit", artifact_types=("csv",)),
        CapabilitySpec(id="jst.product.sync", platform="jst", command="product sync", scenes=("product_export",), artifact_types=("xlsx",)),
        CapabilitySpec(id="jst.product.learn", platform="jst", command="product learn", scenes=("product_export",), recovery_policy="explicit"),
        CapabilitySpec(id="jst.browser.learn", platform="jst", command="browser learn", recovery_policy="explicit"),
        CapabilitySpec(id="jst.shop-goods.import", platform="jst", command="shop-goods import", scenes=("order_list",), artifact_types=("xlsx",)),
        CapabilitySpec(id="jst.order.query", platform="jst", command="order query", scenes=("order_list",)),
        CapabilitySpec(id="jst.order.label", platform="jst", command="order label", scenes=("order_list",)),
        CapabilitySpec(id="jst.order.remark", platform="jst", command="order remark", scenes=("order_list",)),
        CapabilitySpec(id="jst.order.normalize", platform="jst", command="order normalize", scenes=("order_list",)),
        CapabilitySpec(id="jst.order.logistics", platform="jst", command="order logistics", scenes=("order_list", "order_logistics_trace")),
        CapabilitySpec(id="jst.order.logistics.learn", platform="jst", command="order logistics learn", scenes=("order_list", "order_logistics_trace"), recovery_policy="explicit"),
        CapabilitySpec(id="jst.order.pickup-watch", platform="jst", command="order pickup-watch", scenes=("order_list", "order_logistics_trace")),
        CapabilitySpec(id="jst.order.invoice", platform="jst", command="order invoice", scenes=("order_list", "order_invoice_workorder")),
        CapabilitySpec(id="jst.order.invoice.learn", platform="jst", command="order invoice learn", scenes=("order_list", "order_invoice_workorder"), recovery_policy="explicit"),
        CapabilitySpec(id="jst.order.reimburse", platform="jst", command="order reimburse", scenes=("order_list",), artifact_types=("xlsx",)),
        CapabilitySpec(id="jst.order.exchange-resend.learn", platform="jst", command="order exchange-resend learn", scenes=("order_list", "order_exchange_resend"), recovery_policy="explicit"),
        CapabilitySpec(id="jst.order.exchange-resend.preview", platform="jst", command="order exchange-resend preview", scenes=("order_list",)),
        CapabilitySpec(id="jst.order.exchange-resend.submit", platform="jst", command="order exchange-resend submit", scenes=("order_list", "order_exchange_resend")),
        CapabilitySpec(id="jst.order.stats", platform="jst", command="order stats", scenes=("profit_multi_dimension_report",)),
        CapabilitySpec(id="jst.order.stats.learn", platform="jst", command="order stats learn", scenes=("profit_multi_dimension_report",), recovery_policy="explicit"),
    ]:
        capabilities[spec.id] = spec

    # --- Add to parent app ---
    app.add_typer(jst_app, name="jst")
