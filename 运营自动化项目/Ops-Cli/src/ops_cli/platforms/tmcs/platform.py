"""TMCS platform registration — commands and capabilities."""
from __future__ import annotations

import typer

from ops_cli.capabilities import CapabilitySpec
from ops_cli.cli_helpers import _execute
from ops_cli.output import CommandResponse
from ops_cli.platforms.tmcs.auth import check_auth, capture_auth, ensure_auth
from ops_cli.platforms.tmcs.bill import learn_bill_download, run_bill_download
from ops_cli.platforms.tmcs.inventory import (
    learn_inventory_adjust,
    learn_inventory_export,
    run_inventory_adjust,
    run_inventory_export,
)
from ops_cli.platforms.tmcs.fulfillment import (
    learn_fulfillment_overview,
    run_fulfillment_overview,
)
from ops_cli.platforms.tmcs.fund import run_promotion_balance_sum, run_receivable_bill_sum
from ops_cli.platforms.tmcs.price_competitiveness import (
    learn_price_competitiveness_lookup,
    run_price_competitiveness_list,
    run_price_competitiveness_lookup,
)
from ops_cli.platforms.tmcs.zdx import (
    learn_zdx_fullsite_plan_create,
    run_zdx_fullsite_plan_create,
)
from ops_cli.platforms.tmcs.listing import create_listing
from ops_cli.platforms.tmcs.marketing import (
    learn_marketing_risk_warning_count,
    run_marketing_risk_warning_count,
)
from ops_cli.platforms.tmcs.product import learn_product_sync, list_products, run_product_sync
from ops_cli.platforms.tmcs.promotion_bill import learn_promotion_bill, run_promotion_bill_download
from ops_cli.platforms.tmcs.stock import query_stock
from ops_cli.platforms.tmcs.xp_workorder import (
    DEFAULT_THRESHOLD as XP_WORKORDER_DEFAULT_THRESHOLD,
    count_xp_workorders,
    learn_xp_workorder_count,
)


def register(app: typer.Typer, capabilities: dict[str, CapabilitySpec]) -> None:
    tmcs_app = typer.Typer(help="Tmall Chaoshi platform commands.", no_args_is_help=True)
    tmcs_auth_app = typer.Typer(help="TMCS auth commands.", no_args_is_help=True)
    tmcs_product_app = typer.Typer(help="TMCS product commands.", no_args_is_help=True)
    tmcs_inventory_app = typer.Typer(help="TMCS inventory commands.", no_args_is_help=True)
    tmcs_stock_app = typer.Typer(help="TMCS stock query commands.", no_args_is_help=True)
    tmcs_bill_app = typer.Typer(help="TMCS bill commands.", no_args_is_help=True)
    tmcs_promotion_bill_app = typer.Typer(help="TMCS promotion bill commands.", no_args_is_help=True)
    tmcs_listing_app = typer.Typer(help="TMCS listing commands.", no_args_is_help=True)
    tmcs_xp_workorder_app = typer.Typer(help="TMCS XP workorder commands.", no_args_is_help=True)
    tmcs_fulfillment_app = typer.Typer(help="TMCS fulfillment commands.", no_args_is_help=True)
    tmcs_price_competitiveness_app = typer.Typer(help="TMCS price competitiveness commands.", no_args_is_help=True)
    tmcs_fund_app = typer.Typer(help="TMCS fund commands.", no_args_is_help=True)
    tmcs_fund_receivable_bill_app = typer.Typer(help="TMCS receivable bill fund commands.", no_args_is_help=True)
    tmcs_fund_promotion_balance_app = typer.Typer(help="TMCS promotion balance fund commands.", no_args_is_help=True)
    tmcs_marketing_app = typer.Typer(help="TMCS marketing commands.", no_args_is_help=True)
    tmcs_marketing_risk_warning_app = typer.Typer(help="TMCS marketing risk-warning commands.", no_args_is_help=True)
    tmcs_zdx_app = typer.Typer(help="TMCS ZDX (智多星) commands.", no_args_is_help=True)
    tmcs_zdx_fullsite_plan_app = typer.Typer(help="ZDX fullsite plan commands.", no_args_is_help=True)

    # --- Auth ---

    @tmcs_auth_app.command("check")
    def tmcs_auth_check(ctx: typer.Context) -> None:
        _execute(ctx, command_name="ops tmcs auth check", params={}, handler=check_auth)

    @tmcs_auth_app.command("ensure")
    def tmcs_auth_ensure(ctx: typer.Context) -> None:
        _execute(ctx, command_name="ops tmcs auth ensure", params={}, handler=ensure_auth)

    @tmcs_auth_app.command("capture")
    def tmcs_auth_capture(ctx: typer.Context) -> None:
        _execute(ctx, command_name="ops tmcs auth capture", params={}, handler=capture_auth)

    # --- Product ---

    @tmcs_product_app.command("list")
    def tmcs_product_list(ctx: typer.Context) -> None:
        _execute(ctx, command_name="ops tmcs product list", params={}, handler=list_products)

    @tmcs_product_app.command("sync")
    def tmcs_product_sync(
        ctx: typer.Context,
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, do not write latest workbook."),
        use_local_only: bool = typer.Option(False, "--use-local-only", help="Skip backend export and use local import file only."),
        force_refresh: bool = typer.Option(False, "--force-refresh", help="Force refresh from TMCS backend export."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs product sync",
            params={"dry_run": dry_run, "use_local_only": use_local_only, "force_refresh": force_refresh},
            handler=lambda: run_product_sync(
                dry_run=dry_run,
                use_local_only=use_local_only,
                force_refresh=force_refresh,
            ),
        )

    @tmcs_product_app.command("learn")
    def tmcs_product_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs product learn",
            params={"force": force},
            handler=lambda: learn_product_sync(force=force),
        )

    # --- Inventory ---

    @tmcs_inventory_app.command("export")
    def tmcs_inventory_export(
        ctx: typer.Context,
        warehouse_code: str = typer.Option("mc_aokesi_suolong", "--warehouse-code", help="Merchant warehouse code."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, do not download files."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs inventory export",
            params={"warehouse_code": warehouse_code, "dry_run": dry_run},
            handler=lambda: run_inventory_export(warehouse_code=warehouse_code, dry_run=dry_run),
        )

    @tmcs_inventory_app.command("learn")
    def tmcs_inventory_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs inventory learn",
            params={"force": force},
            handler=lambda: learn_inventory_export(force=force),
        )

    @tmcs_inventory_app.command("adjust", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
    def tmcs_inventory_adjust(
        ctx: typer.Context,
        action: str = typer.Option(..., "--action", help="increase, decrease, or clear."),
        sku_id: str | None = typer.Option(None, "--sku-id", help="Platform SKU ID."),
        item_id: str | None = typer.Option(None, "--item-id", help="Platform item ID."),
        quantity: int | None = typer.Option(None, "--quantity", help="Quantity for increase/decrease."),
        sku_adjust: list[str] | None = typer.Option(None, "--sku-adjust", help="Batch SKU:quantity item. Repeatable."),
        warehouse_code: str = typer.Option("mc_aokesi_suolong", "--warehouse-code", help="Merchant warehouse code."),
        execute: bool = typer.Option(False, "--execute", help="Actually submit inventory adjustment."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs inventory adjust",
            params={
                "action": action,
                "sku_id": sku_id,
                "item_id": item_id,
                "quantity": quantity,
                "sku_adjust": sku_adjust or [],
                "warehouse_code": warehouse_code,
                "execute": execute,
            },
            handler=lambda: run_inventory_adjust(
                action=action,
                sku_id=sku_id,
                item_id=item_id,
                quantity=quantity,
                sku_adjust=sku_adjust or [],
                warehouse_code=warehouse_code,
                execute=execute,
            ),
        )

    @tmcs_inventory_app.command("adjust-learn")
    def tmcs_inventory_adjust_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs inventory adjust-learn",
            params={"force": force},
            handler=lambda: learn_inventory_adjust(force=force),
        )

    # --- Stock ---

    @tmcs_stock_app.command("query")
    def tmcs_stock_query(
        ctx: typer.Context,
        item_ids: str = typer.Option(..., "--item-ids", help="Comma-separated TMCS platform item IDs."),
        warehouse_code: str = typer.Option("mc_aokesi_suolong", "--warehouse-code", help="TMCS merchant warehouse code."),
        output: str = typer.Option("json", "--output", help="Output format. Currently only json is supported."),
    ) -> None:
        def handler() -> CommandResponse:
            if output.lower() != "json":
                raise RuntimeError("tmcs stock query 当前仅支持 --output json。")
            return CommandResponse(
                success=True,
                platform="tmcs",
                command="stock query",
                data={"rows": query_stock(item_ids=item_ids, warehouse_code=warehouse_code)},
            )

        _execute(
            ctx,
            command_name="ops tmcs stock query",
            params={"item_ids": item_ids, "warehouse_code": warehouse_code, "output": output},
            handler=handler,
            force_json=True,
        )

    # --- Bill ---

    @tmcs_bill_app.command("download")
    def tmcs_bill_download(
        ctx: typer.Context,
        start: str | None = typer.Option(None, "--start", help="Start date in YYYY-MM-DD."),
        end: str | None = typer.Option(None, "--end", help="End date in YYYY-MM-DD."),
        last_month: bool = typer.Option(False, "--last-month", help="Download previous month's bills, querying through month-end + 3 days."),
        download_statement_list: bool = typer.Option(False, "--download-statement-list", help="Also download 对账单列表.xlsx."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, do not download files."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs bill download",
            params={
                "start": start,
                "end": end,
                "last_month": last_month,
                "download_statement_list": download_statement_list,
                "dry_run": dry_run,
            },
            handler=lambda: run_bill_download(
                start=start,
                end=end,
                last_month=last_month,
                download_statement_list=download_statement_list,
                dry_run=dry_run,
            ),
        )

    @tmcs_bill_app.command("learn")
    def tmcs_bill_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs bill learn",
            params={"force": force},
            handler=lambda: learn_bill_download(force=force),
        )

    # --- Promotion Bill ---

    @tmcs_promotion_bill_app.command("download")
    def tmcs_promotion_bill_download(
        ctx: typer.Context,
        source: str = typer.Option("all", "--source", help="all, zdx, or wxt."),
        start: str | None = typer.Option(None, "--start", help="Start date in YYYY-MM-DD."),
        end: str | None = typer.Option(None, "--end", help="End date in YYYY-MM-DD."),
        last_month: bool = typer.Option(False, "--last-month", help="Download previous natural month's promotion bills."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, do not download files."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs promotion-bill download",
            params={"source": source, "start": start, "end": end, "last_month": last_month, "dry_run": dry_run},
            handler=lambda: run_promotion_bill_download(
                source=source,
                start=start,
                end=end,
                last_month=last_month,
                dry_run=dry_run,
            ),
        )

    @tmcs_promotion_bill_app.command("learn")
    def tmcs_promotion_bill_learn(
        ctx: typer.Context,
        source: str = typer.Option("all", "--source", help="all, zdx, or wxt."),
        force: bool = typer.Option(False, "--force", help="Force recapture even if scene exists."),
        timeout: int = typer.Option(90, "--timeout", help="Seconds to wait for primary Chrome export request capture."),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs promotion-bill learn",
            params={"source": source, "force": force, "timeout": timeout},
            handler=lambda: learn_promotion_bill(source=source, force=force, timeout=timeout),
        )

    # --- Listing ---

    @tmcs_listing_app.command("create")
    def tmcs_listing_create(ctx: typer.Context) -> None:
        _execute(ctx, command_name="ops tmcs listing create", params={}, handler=create_listing)

    # --- XP Workorder ---

    @tmcs_xp_workorder_app.command("count")
    def tmcs_xp_workorder_count(
        ctx: typer.Context,
        threshold: int = typer.Option(
            XP_WORKORDER_DEFAULT_THRESHOLD,
            "--threshold",
            help="工单数量阈值，超过则 exceeded=true。",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="不读取 scene、不请求平台，返回模拟工单数量。"
        ),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs xp-workorder count",
            params={"threshold": threshold, "dry_run": dry_run},
            handler=lambda: count_xp_workorders(threshold=threshold, dry_run=dry_run),
        )

    @tmcs_xp_workorder_app.command("learn")
    def tmcs_xp_workorder_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="即使 scene 存在也重新捕获。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs xp-workorder learn",
            params={"force": force},
            handler=lambda: learn_xp_workorder_count(force=force),
        )

    # --- Fulfillment ---

    @tmcs_fulfillment_app.command("overview")
    def tmcs_fulfillment_overview(
        ctx: typer.Context,
        dry_run: bool = typer.Option(
            False, "--dry-run", help="不访问页面、不处理平台数据，返回模拟履约指标。"
        ),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs fulfillment overview",
            params={"dry_run": dry_run},
            handler=lambda: run_fulfillment_overview(dry_run=dry_run),
        )

    @tmcs_fulfillment_app.command("learn")
    def tmcs_fulfillment_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="即使 scene 存在也重新捕获。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs fulfillment learn",
            params={"force": force},
            handler=lambda: learn_fulfillment_overview(force=force),
        )

    # --- Price competitiveness ---

    @tmcs_price_competitiveness_app.command("lookup")
    def tmcs_price_competitiveness_lookup(
        ctx: typer.Context,
        product_code: str = typer.Option(..., "--product-code", help="商品编码（猫超 ItemID）。"),
        screenshot_dir: str | None = typer.Option(
            None, "--screenshot-dir", help="可选：截图存证目录；不传则不截图（无人值守更安全）。"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="不访问页面、不处理平台数据，返回模拟查询结果。"
        ),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs price-competitiveness lookup",
            params={"product_code": product_code, "screenshot_dir": screenshot_dir, "dry_run": dry_run},
            handler=lambda: run_price_competitiveness_lookup(
                product_code=product_code,
                screenshot_dir=screenshot_dir,
                dry_run=dry_run,
            ),
        )

    @tmcs_price_competitiveness_app.command("list")
    def tmcs_price_competitiveness_list(
        ctx: typer.Context,
        screenshot_dir: str | None = typer.Option(
            None, "--screenshot-dir", help="可选：截图存证目录；不传则不截图。"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="不访问页面、不处理平台数据，返回模拟空列表。"
        ),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs price-competitiveness list",
            params={"screenshot_dir": screenshot_dir, "dry_run": dry_run},
            handler=lambda: run_price_competitiveness_list(
                screenshot_dir=screenshot_dir,
                dry_run=dry_run,
            ),
        )

    @tmcs_price_competitiveness_app.command("learn")
    def tmcs_price_competitiveness_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="即使 scene 存在也重新捕获。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs price-competitiveness learn",
            params={"force": force},
            handler=lambda: learn_price_competitiveness_lookup(force=force),
        )

    # --- Fund ---

    @tmcs_fund_receivable_bill_app.command("sum")
    def tmcs_fund_receivable_bill_sum(
        ctx: typer.Context,
        month: str = typer.Option(..., "--month", help="Bill month in YYYY-MM."),
        screenshot_dir: str = typer.Option(..., "--screenshot-dir", help="Directory to save proof screenshots."),
        dry_run: bool = typer.Option(False, "--dry-run", help="返回模拟金额和占位截图，不访问猫超页面。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs fund receivable-bill sum",
            params={"month": month, "screenshot_dir": screenshot_dir, "dry_run": dry_run},
            handler=lambda: run_receivable_bill_sum(month=month, screenshot_dir=screenshot_dir, dry_run=dry_run),
        )

    @tmcs_fund_promotion_balance_app.command("sum")
    def tmcs_fund_promotion_balance_sum(
        ctx: typer.Context,
        screenshot_dir: str = typer.Option(..., "--screenshot-dir", help="Directory to save proof screenshots."),
        dry_run: bool = typer.Option(False, "--dry-run", help="返回模拟金额和占位截图，不访问猫超页面。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs fund promotion-balance sum",
            params={"screenshot_dir": screenshot_dir, "dry_run": dry_run},
            handler=lambda: run_promotion_balance_sum(screenshot_dir=screenshot_dir, dry_run=dry_run),
        )

    # --- Marketing risk warning ---

    @tmcs_marketing_risk_warning_app.command("count")
    def tmcs_marketing_risk_warning_count(
        ctx: typer.Context,
        dry_run: bool = typer.Option(
            False, "--dry-run", help="不访问页面、不处理平台数据，返回模拟风险预警数值。"
        ),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs marketing risk-warning count",
            params={"dry_run": dry_run},
            handler=lambda: run_marketing_risk_warning_count(dry_run=dry_run),
        )

    @tmcs_marketing_risk_warning_app.command("learn")
    def tmcs_marketing_risk_warning_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="即使 scene 存在也重新捕获。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs marketing risk-warning learn",
            params={"force": force},
            handler=lambda: learn_marketing_risk_warning_count(force=force),
        )

    # --- ZDX (智多星) ---

    @tmcs_zdx_fullsite_plan_app.command("create")
    def tmcs_zdx_fullsite_plan_create(
        ctx: typer.Context,
        item_id: str = typer.Option(..., "--item-id", help="天猫平台商品ID（商品编码）。"),
        plan_name: str = typer.Option(..., "--plan-name", help="推广计划名称。"),
        daily_budget: float = typer.Option(..., "--daily-budget", help="每日预算金额（元）。"),
        target_roi: float = typer.Option(..., "--target-roi", help="目标投产比 ROI。"),
        execute: bool = typer.Option(False, "--execute", help="真实创建计划，必须显式传入。"),
        dry_run: bool = typer.Option(False, "--dry-run", help="预览计划内容，不真实创建。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs zdx fullsite-plan create",
            params={
                "item_id": item_id,
                "plan_name": plan_name,
                "daily_budget": daily_budget,
                "target_roi": target_roi,
                "execute": execute,
                "dry_run": dry_run,
            },
            handler=lambda: run_zdx_fullsite_plan_create(
                item_id=item_id,
                plan_name=plan_name,
                daily_budget=daily_budget,
                target_roi=target_roi,
                execute=execute,
                dry_run=dry_run,
            ),
        )

    @tmcs_zdx_fullsite_plan_app.command("learn")
    def tmcs_zdx_fullsite_plan_learn(
        ctx: typer.Context,
        force: bool = typer.Option(False, "--force", help="即使 scene 存在也重新捕获。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmcs zdx fullsite-plan learn",
            params={"force": force},
            handler=lambda: learn_zdx_fullsite_plan_create(force=force),
        )

    # --- Wire up Typer hierarchy ---

    tmcs_app.add_typer(tmcs_auth_app, name="auth")
    tmcs_app.add_typer(tmcs_product_app, name="product")
    tmcs_app.add_typer(tmcs_inventory_app, name="inventory")
    tmcs_app.add_typer(tmcs_stock_app, name="stock")
    tmcs_app.add_typer(tmcs_bill_app, name="bill")
    tmcs_app.add_typer(tmcs_promotion_bill_app, name="promotion-bill")
    tmcs_app.add_typer(tmcs_listing_app, name="listing")
    tmcs_app.add_typer(tmcs_xp_workorder_app, name="xp-workorder")
    tmcs_app.add_typer(tmcs_fulfillment_app, name="fulfillment")
    tmcs_app.add_typer(tmcs_price_competitiveness_app, name="price-competitiveness")
    tmcs_fund_app.add_typer(tmcs_fund_receivable_bill_app, name="receivable-bill")
    tmcs_fund_app.add_typer(tmcs_fund_promotion_balance_app, name="promotion-balance")
    tmcs_app.add_typer(tmcs_fund_app, name="fund")
    tmcs_marketing_app.add_typer(tmcs_marketing_risk_warning_app, name="risk-warning")
    tmcs_app.add_typer(tmcs_marketing_app, name="marketing")
    tmcs_zdx_app.add_typer(tmcs_zdx_fullsite_plan_app, name="fullsite-plan")
    tmcs_app.add_typer(tmcs_zdx_app, name="zdx")

    # --- Register capabilities ---

    for spec in [
        CapabilitySpec(id="tmcs.auth.check", platform="tmcs", command="auth check", scenes=("maochao_item_search",), recovery_policy="never"),
        CapabilitySpec(id="tmcs.auth.ensure", platform="tmcs", command="auth ensure", scenes=("maochao_item_search",)),
        CapabilitySpec(id="tmcs.auth.capture", platform="tmcs", command="auth capture", scenes=("maochao_item_search",), recovery_policy="explicit"),
        CapabilitySpec(id="tmcs.product.list", platform="tmcs", command="product list", recovery_policy="never"),
        CapabilitySpec(id="tmcs.product.sync", platform="tmcs", command="product sync", scenes=("maochao_item_search", "maochao_item_export"), artifact_types=("xlsx",)),
        CapabilitySpec(id="tmcs.product.learn", platform="tmcs", command="product learn", scenes=("maochao_item_search", "maochao_item_export"), recovery_policy="explicit"),
        CapabilitySpec(id="tmcs.inventory.export", platform="tmcs", command="inventory export", scenes=("maochao_inventory_search", "maochao_inventory_export"), artifact_types=("xlsx",)),
        CapabilitySpec(id="tmcs.inventory.learn", platform="tmcs", command="inventory learn", scenes=("maochao_inventory_search", "maochao_inventory_export"), recovery_policy="explicit"),
        CapabilitySpec(id="tmcs.inventory.adjust", platform="tmcs", command="inventory adjust", scenes=("maochao_inventory_search",)),
        CapabilitySpec(id="tmcs.inventory.adjust-learn", platform="tmcs", command="inventory adjust-learn", scenes=("maochao_inventory_search",), recovery_policy="explicit"),
        CapabilitySpec(id="tmcs.stock.query", platform="tmcs", command="stock query", scenes=("maochao_inventory_search",)),
        CapabilitySpec(id="tmcs.bill.download", platform="tmcs", command="bill download", scenes=("statement_bill_list_for_supplier", "statement_bill_dynamic_list", "download_file_query"), artifact_types=("xlsx",)),
        CapabilitySpec(id="tmcs.bill.learn", platform="tmcs", command="bill learn", scenes=("statement_bill_list_for_supplier", "statement_bill_dynamic_list", "download_file_query"), recovery_policy="explicit"),
        CapabilitySpec(id="tmcs.promotion-bill.download", platform="tmcs", command="promotion-bill download", scenes=("tmcs_promotion_zdx_bill_export", "tmcs_promotion_wxt_bill_export", "download_file_query"), artifact_types=("xlsx", "csv")),
        CapabilitySpec(id="tmcs.promotion-bill.learn", platform="tmcs", command="promotion-bill learn", scenes=("tmcs_promotion_zdx_bill_export", "tmcs_promotion_wxt_bill_export", "download_file_query"), recovery_policy="explicit"),
        CapabilitySpec(id="tmcs.listing.create", platform="tmcs", command="listing create"),
        CapabilitySpec(
            id="tmcs.xp-workorder.count",
            platform="tmcs",
            command="xp-workorder count",
            scenes=("xp_workorder_count",),
        ),
        CapabilitySpec(
            id="tmcs.xp-workorder.learn",
            platform="tmcs",
            command="xp-workorder learn",
            scenes=("xp_workorder_count",),
            recovery_policy="explicit",
        ),
        CapabilitySpec(
            id="tmcs.fulfillment.overview",
            platform="tmcs",
            command="fulfillment overview",
            scenes=("fulfillment_overview",),
        ),
        CapabilitySpec(
            id="tmcs.fulfillment.learn",
            platform="tmcs",
            command="fulfillment learn",
            scenes=("fulfillment_overview",),
            recovery_policy="explicit",
        ),
        CapabilitySpec(
            id="tmcs.price-competitiveness.lookup",
            platform="tmcs",
            command="price-competitiveness lookup",
            scenes=("price_competitiveness_lookup",),
        ),
        CapabilitySpec(
            id="tmcs.price-competitiveness.list",
            platform="tmcs",
            command="price-competitiveness list",
            scenes=("price_competitiveness_lookup",),
        ),
        CapabilitySpec(
            id="tmcs.price-competitiveness.learn",
            platform="tmcs",
            command="price-competitiveness learn",
            scenes=("price_competitiveness_lookup",),
            recovery_policy="explicit",
        ),
        CapabilitySpec(
            id="tmcs.fund.receivable-bill.sum",
            platform="tmcs",
            command="fund receivable-bill sum",
            scenes=("fund_receivable_bill_sum",),
            artifact_types=("png",),
        ),
        CapabilitySpec(
            id="tmcs.fund.promotion-balance.sum",
            platform="tmcs",
            command="fund promotion-balance sum",
            scenes=("fund_promotion_balance_sum",),
            artifact_types=("png",),
        ),
        CapabilitySpec(
            id="tmcs.marketing.risk-warning.count",
            platform="tmcs",
            command="marketing risk-warning count",
            scenes=("marketing_risk_warning_count",),
        ),
        CapabilitySpec(
            id="tmcs.marketing.risk-warning.learn",
            platform="tmcs",
            command="marketing risk-warning learn",
            scenes=("marketing_risk_warning_count",),
            recovery_policy="explicit",
        ),
        CapabilitySpec(
            id="tmcs.zdx.fullsite-plan.create",
            platform="tmcs",
            command="zdx fullsite-plan create",
            scenes=("zdx_fullsite_plan_create",),
        ),
        CapabilitySpec(
            id="tmcs.zdx.fullsite-plan.learn",
            platform="tmcs",
            command="zdx fullsite-plan learn",
            scenes=("zdx_fullsite_plan_create",),
            recovery_policy="explicit",
        ),
    ]:
        capabilities[spec.id] = spec

    # --- Add to parent app ---
    app.add_typer(tmcs_app, name="tmcs")
