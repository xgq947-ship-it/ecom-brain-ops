"""Tests for tmcs zdx fullsite-plan create capability."""
from __future__ import annotations

import pytest

from ops_cli.capabilities import capability_ids, get_capability
from ops_cli.cli import app  # noqa: F401  # 触发平台能力注册
from ops_cli.platforms.tmcs import zdx


def test_zdx_capabilities_registered() -> None:
    ids = capability_ids()
    assert "tmcs.zdx.fullsite-plan.create" in ids
    assert "tmcs.zdx.fullsite-plan.learn" in ids


def test_zdx_create_spec() -> None:
    spec = get_capability("tmcs.zdx.fullsite-plan.create")
    assert spec.platform == "tmcs"
    assert spec.command == "zdx fullsite-plan create"
    assert "zdx_fullsite_plan_create" in spec.scenes


def test_zdx_learn_spec() -> None:
    spec = get_capability("tmcs.zdx.fullsite-plan.learn")
    assert spec.platform == "tmcs"
    assert spec.command == "zdx fullsite-plan learn"
    assert spec.recovery_policy == "explicit"


def test_dry_run_returns_not_created(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = zdx.run_zdx_fullsite_plan_create(
        item_id="123456789",
        plan_name="全站推广_123456789_0602",
        daily_budget=100.0,
        target_roi=3.5,
        execute=False,
        dry_run=True,
    )

    assert response.success is True
    assert response.platform == "tmcs"
    assert response.command == "zdx fullsite-plan create"
    data = response.data
    assert data["executed"] is False
    assert data["created"] is False
    assert data["dry_run"] is True
    assert data["simulated"] is True
    assert data["item_id"] == "123456789"
    assert data["plan_name"] == "全站推广_123456789_0602"
    assert data["daily_budget"] == 100.0
    assert data["target_roi"] == 3.5
    assert data["scene"].endswith("/zdx_fullsite_plan_create")
    assert data["context_path"].endswith(".json")


def test_no_execute_returns_not_created(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = zdx.run_zdx_fullsite_plan_create(
        item_id="999",
        plan_name="全站推广_999_0101",
        daily_budget=50.0,
        target_roi=2.0,
        execute=False,
        dry_run=False,
    )

    assert response.success is True
    assert response.data["executed"] is False
    assert response.data["created"] is False


def test_learn_returns_note(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = zdx.learn_zdx_fullsite_plan_create(force=False)

    assert response.success is True
    assert response.platform == "tmcs"
    assert response.command == "zdx fullsite-plan learn"
    data = response.data
    assert "zdx_fullsite_plan_create" in data["scene"]
    assert "note" in data
    assert "next_command" in data


def test_scene_constant() -> None:
    assert zdx.ZDX_FULLSITE_PLAN_CREATE_SCENE == "zdx_fullsite_plan_create"
