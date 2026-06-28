"""Tmall platform registration — public item-page price reading commands."""
from __future__ import annotations

import typer

from ops_cli.capabilities import CapabilitySpec
from ops_cli.cli_helpers import _execute
from ops_cli.platforms.tmall.item_price import run_item_price


def register(app: typer.Typer, capabilities: dict[str, CapabilitySpec]) -> None:
    tmall_app = typer.Typer(help="Tmall public item-page commands.", no_args_is_help=True)
    tmall_price_app = typer.Typer(help="Tmall item price commands.", no_args_is_help=True)

    @tmall_price_app.command("get")
    def tmall_price_get(
        ctx: typer.Context,
        item_ids: str = typer.Option(..., "--item-ids", help="Comma-separated Tmall item IDs or detail URLs."),
        screenshot_dir: str = typer.Option(..., "--screenshot-dir", help="Directory to save proof screenshots."),
        dry_run: bool = typer.Option(False, "--dry-run", help="返回模拟价格与占位截图，不访问天猫页面。"),
    ) -> None:
        _execute(
            ctx,
            command_name="ops tmall price get",
            params={"item_ids": item_ids, "screenshot_dir": screenshot_dir, "dry_run": dry_run},
            handler=lambda: run_item_price(item_ids=item_ids, screenshot_dir=screenshot_dir, dry_run=dry_run),
        )

    tmall_app.add_typer(tmall_price_app, name="price")

    # 公开商品页价格读取依赖主浏览器登录态，不走 scene 自动恢复；登录/滑块由结果状态显式上报。
    capabilities["tmall.price.get"] = CapabilitySpec(
        id="tmall.price.get",
        platform="tmall",
        command="price get",
        recovery_policy="never",
        artifact_types=("png",),
    )

    app.add_typer(tmall_app, name="tmall")
