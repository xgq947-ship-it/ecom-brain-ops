from __future__ import annotations

import pytest

from ops_cli.capabilities import capability_ids, get_capability
from ops_cli.cli import app  # noqa: F401
from ops_cli.platforms.tmcs import marketing


def test_marketing_risk_warning_capability_registered() -> None:
    registered = capability_ids()
    assert "tmcs.marketing.risk-warning.count" in registered
    assert "tmcs.marketing.risk-warning.learn" in registered

    spec = get_capability("tmcs.marketing.risk-warning.count")
    assert spec.platform == "tmcs"
    assert spec.command == "marketing risk-warning count"
    assert "marketing_risk_warning_count" in spec.scenes


def test_extract_count_zero() -> None:
    assert marketing.extract_risk_warning_count("营销活动中心 风险预警（0） 进行中") == 0


def test_extract_count_three() -> None:
    assert marketing.extract_risk_warning_count("风险预警（3）") == 3


def test_extract_count_half_width_parens() -> None:
    assert marketing.extract_risk_warning_count("风险预警(3)") == 3


def test_extract_count_badge_form_real_dom() -> None:
    # 真实营销活动中心 DOM 文本：「风险预警」标签 + 数字徽标在下一行。
    dom_text = "专属邀约\n0\n暂无数据\n风险预警\n0\n暂无数据\n推广建议\n0\n暂无数据"
    assert marketing.extract_risk_warning_count(dom_text) == 0


def test_extract_count_badge_form_nonzero() -> None:
    assert marketing.extract_risk_warning_count("风险预警\n3\n暂无数据") == 3


def test_extract_count_missing_returns_none() -> None:
    assert marketing.extract_risk_warning_count("营销活动中心 暂无数据") is None


# 2026-06 改版：「重要事项」卡片为空时平台只渲染「暂无数据」、不再列「风险预警（0）」。
_EMPTY_PAGE_TEXT = "Hi，欢迎来到营销活动中心 查看旧版 重要事项 全部 暂无数据 全部活动 活动日历"


def test_resolve_empty_important_items_is_zero() -> None:
    # 卡片空 = 0 条风险预警，按 0 处理而非报错
    assert marketing.resolve_risk_warning_count(_EMPTY_PAGE_TEXT) == 0
    assert marketing._important_items_empty(_EMPTY_PAGE_TEXT) is True


def test_resolve_warning_present_takes_precedence() -> None:
    assert marketing.resolve_risk_warning_count("营销活动中心 重要事项 风险预警（3）") == 3


def test_resolve_load_failure_not_treated_as_zero() -> None:
    # 没加载到营销活动中心（如登录页/白屏）→ 不能误判成 0，仍返回 None
    assert marketing.resolve_risk_warning_count("登录 请先登录") is None
    assert marketing.resolve_risk_warning_count("重要事项 暂无数据") is None  # 缺“营销活动中心”锚点
    assert marketing._important_items_empty("重要事项 暂无数据") is False


def test_detect_tmcs_login_page() -> None:
    assert marketing._is_login_page("https://login.taobao.com/member/login.jhtml") is True
    assert marketing._is_login_page("https://web.txcs.tmall.com/") is False


def test_count_dry_run_returns_simulated(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = marketing.run_marketing_risk_warning_count(dry_run=True)

    assert response.success is True
    assert response.platform == "tmcs"
    assert response.command == "marketing risk-warning count"
    data = response.data
    assert data["risk_warning_count"] == 0
    assert data["label_text"] == "风险预警（0）"
    assert data["source"] == "simulated"
    assert data["simulated"] is True
    assert data["dry_run"] is True
    assert data["scene"].endswith("/marketing_risk_warning_count")
    assert data["context_path"].endswith(".json")


def test_count_reads_page_and_returns_value(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        marketing, "_read_marketing_page_text", lambda: "营销活动中心 风险预警（3） 进行中"
    )

    response = marketing.run_marketing_risk_warning_count(dry_run=False)

    assert response.success is True
    data = response.data
    assert data["risk_warning_count"] == 3
    assert data["label_text"] == "风险预警（3）"
    assert data["source"] == "page"
    assert data["simulated"] is False
    assert data["dry_run"] is False


def test_count_zero_from_page(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(marketing, "_read_marketing_page_text", lambda: "风险预警（0）")

    response = marketing.run_marketing_risk_warning_count(dry_run=False)
    assert response.data["risk_warning_count"] == 0


def test_count_missing_raises_risk_warning_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(marketing, "_read_marketing_page_text", lambda: "营销活动中心 暂无数据")

    with pytest.raises(RuntimeError, match="RISK_WARNING_COUNT_NOT_FOUND"):
        marketing.run_marketing_risk_warning_count(dry_run=False)


def test_learn_is_noop_page_dom(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = marketing.learn_marketing_risk_warning_count()

    assert response.success is True
    assert response.data["mode"] == "page_dom"
    assert response.data["scene"] == "marketing_risk_warning_count"
