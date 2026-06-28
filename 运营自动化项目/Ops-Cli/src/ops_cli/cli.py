"""Ecommerce operations CLI — auto-discovers platform commands."""
from __future__ import annotations

import importlib
from pathlib import Path

import typer

from ops_cli.browser import check_browser_port, cleanup_browser_tabs, list_browser_tabs
from ops_cli.capabilities import CapabilitySpec, register_capabilities
from ops_cli.cli_helpers import _execute


app = typer.Typer(help="Ecommerce operations CLI.", no_args_is_help=True)


def _discover_and_register_platforms(app: typer.Typer) -> None:
    """Scan platforms/ for platform.py modules and call their register()."""
    platforms_dir = Path(__file__).resolve().parent / "platforms"
    capabilities: dict[str, CapabilitySpec] = {}

    for platform_dir in sorted(platforms_dir.iterdir()):
        if not platform_dir.is_dir() or platform_dir.name.startswith("_"):
            continue
        platform_file = platform_dir / "platform.py"
        if not platform_file.exists():
            continue
        mod = importlib.import_module(f"ops_cli.platforms.{platform_dir.name}.platform")
        mod.register(app, capabilities)

    # Register all collected capabilities with the global registry
    register_capabilities(list(capabilities.values()))


# Browser command (not platform-specific, stays in cli.py)
browser_app = typer.Typer(help="Browser utility commands.", no_args_is_help=True)


@browser_app.command("check")
def browser_check(
    ctx: typer.Context,
    port: int = typer.Option(9222, "--port", help="Chrome remote debugging port."),
) -> None:
    _execute(ctx, command_name="ops browser check", params={"port": port}, handler=lambda: check_browser_port(port))


@browser_app.command("tabs")
def browser_tabs(
    ctx: typer.Context,
    port: int = typer.Option(9222, "--port", help="Chrome remote debugging port."),
) -> None:
    _execute(ctx, command_name="ops browser tabs", params={"port": port}, handler=lambda: list_browser_tabs(port))


@browser_app.command("cleanup")
def browser_cleanup(
    ctx: typer.Context,
    port: int = typer.Option(9222, "--port", help="Chrome remote debugging port."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview tabs to close without closing them."),
) -> None:
    _execute(
        ctx,
        command_name="ops browser cleanup",
        params={"port": port, "dry_run": dry_run},
        handler=lambda: cleanup_browser_tabs(port, dry_run=dry_run),
    )


# Register browser capability
register_capabilities(
    [
        CapabilitySpec(id="browser.check", platform="browser", command="check", recovery_policy="never"),
        CapabilitySpec(id="browser.tabs", platform="browser", command="tabs", recovery_policy="never"),
        CapabilitySpec(id="browser.cleanup", platform="browser", command="cleanup", recovery_policy="never"),
    ]
)

app.add_typer(browser_app, name="browser")

# Discover and register all platforms
_discover_and_register_platforms(app)


@app.callback()
def main_callback(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Output JSON."),
    interactive_login: bool | None = typer.Option(
        None,
        "--interactive-login/--no-interactive-login",
        help="Override terminal detection for SessionHub login recovery.",
    ),
) -> None:
    ctx.obj = {"json_output": json_output, "interactive_login": interactive_login}


def main() -> None:
    app()
