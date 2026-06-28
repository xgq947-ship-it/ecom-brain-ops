"""Tests for platform auto-discovery and registration."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ops_cli.capabilities import CapabilitySpec, capability_for_command, capability_ids, register_capabilities
from ops_cli.cli import app


runner = CliRunner()


def test_discover_platforms_finds_jst_and_tmcs() -> None:
    """Platform directories with platform.py should be discoverable."""
    platforms_dir = Path(__file__).resolve().parent.parent / "src" / "ops_cli" / "platforms"
    found = set()
    for platform_dir in sorted(platforms_dir.iterdir()):
        if not platform_dir.is_dir() or platform_dir.name.startswith("_"):
            continue
        if (platform_dir / "platform.py").exists():
            found.add(platform_dir.name)
    assert found == {"jst", "tmcs", "tmall"}


def test_register_creates_typer_subcommands() -> None:
    """After registration, jst and tmcs subcommands exist in the app."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "jst" in result.stdout
    assert "tmcs" in result.stdout


def test_jst_subcommands_available() -> None:
    result = runner.invoke(app, ["jst", "--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "profit" in result.stdout
    assert "product" in result.stdout
    assert "order" in result.stdout


def test_tmcs_subcommands_available() -> None:
    result = runner.invoke(app, ["tmcs", "--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "product" in result.stdout
    assert "inventory" in result.stdout
    assert "bill" in result.stdout


def test_capabilities_populated_after_registration() -> None:
    """All expected capability IDs should be registered."""
    ids = capability_ids()
    assert "jst.auth.check" in ids
    assert "jst.profit.yesterday" in ids
    assert "tmcs.auth.check" in ids
    assert "tmcs.product.list" in ids
    assert "tmcs.fulfillment.overview" in ids
    assert "tmcs.fund.receivable-bill.sum" in ids
    assert "tmcs.fund.promotion-balance.sum" in ids
    assert "browser.check" in ids
    assert "jst.report.product-profit.export" in ids
    assert "jst.report.product-profit.learn" in ids
    assert "tmcs.marketing.risk-warning.count" in ids
    assert "jst.auth.sms.detect" in ids
    assert "jst.auth.sms.submit" in ids
    assert "jst.order.exchange-resend.learn" in ids
    assert "jst.order.exchange-resend.preview" in ids
    assert "jst.order.exchange-resend.submit" in ids
    assert "tmall.price.get" in ids
    assert "jst.order.normalize" in ids
    assert "tmcs.price-competitiveness.lookup" in ids
    assert "tmcs.price-competitiveness.list" in ids
    assert "tmcs.price-competitiveness.learn" in ids
    assert "jst.profit.day" in ids
    assert "browser.tabs" in ids
    assert "browser.cleanup" in ids
    # Total: previous 61 + browser.tabs + browser.cleanup
    assert len(ids) == 63


def test_capability_for_command_works_after_registration() -> None:
    spec = capability_for_command("jst", "auth check")
    assert spec.platform == "jst"
    assert spec.command == "auth check"
    assert spec.recovery_policy == "never"

    spec = capability_for_command("tmcs", "bill download")
    assert spec.platform == "tmcs"
    assert "xlsx" in spec.artifact_types


def test_platform_module_has_register_function() -> None:
    """Each platform.py should export a register() function."""
    for platform_name in ("jst", "tmcs"):
        mod = importlib.import_module(f"ops_cli.platforms.{platform_name}.platform")
        assert callable(getattr(mod, "register", None)), f"{platform_name}.platform missing register()"
