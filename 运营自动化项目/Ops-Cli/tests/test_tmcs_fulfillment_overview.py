from __future__ import annotations

import pytest

from ops_cli.capabilities import capability_ids, get_capability
from ops_cli.cli import app  # noqa: F401
from ops_cli.platforms.tmcs import fulfillment


# 贴近真实「日常考核 / 数据概览」页 inner_text 的样本（含考核表现预警横幅）。
REAL_TEXT_SAMPLE = (
    "考核表现 亲爱的供应商伙伴，您被判定为 心智上门商家，请关注送货上门率（需>= 75%）、"
    "24H支揽率（需>=95%）上一个周期20260427-20260524的表现为B类警告 "
    "连续1个考核周期不达标，请及时整改 "
    "数据概览 "
    "24H支揽率（T+2） 99.43 % 对比 6.33pt 异常仓 0 异常单据 3 查看 "
    "48H支揽率（T+3） 100.00 % 对比 0.13pt 异常单据 0 "
    "送货上门率 92.59 % 对比 -1.71pt 异常仓 0 "
    "隔日达率 53.97 % 对比 -3.52pt "
    "4CP占比 99.47 % 对比 -0.40pt "
    "4CP占比_剔偏远 99.46 % 对比 -0.41pt "
    "预警单据总量 -"
)


def test_fulfillment_capability_registered() -> None:
    registered = capability_ids()
    assert "tmcs.fulfillment.overview" in registered
    assert "tmcs.fulfillment.learn" in registered

    spec = get_capability("tmcs.fulfillment.overview")
    assert spec.platform == "tmcs"
    assert spec.command == "fulfillment overview"
    assert "fulfillment_overview" in spec.scenes


def test_dry_run_returns_simulated(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = fulfillment.run_fulfillment_overview(dry_run=True)

    assert response.success is True
    assert response.platform == "tmcs"
    assert response.command == "fulfillment overview"
    data = response.data
    assert data["simulated"] is True
    assert data["source"] == "simulated"
    assert data["dry_run"] is True
    assert data["weekly_warning_level"] is None
    assert data["scene"].endswith("/fulfillment_overview")
    assert data["context_path"].endswith(".json")
    metrics = data["metrics"]
    for key in fulfillment.METRIC_KEYS:
        assert key in metrics
    assert metrics["pickup_24h_rate"] == 99.43
    assert metrics["four_cp_rate"] == 99.47
    assert "seven_cp_rate" not in metrics
    assert metrics["exception_feedback_required"] is False


def test_extract_metrics_from_real_text() -> None:
    metrics, weekly = fulfillment.extract_metrics_from_text(REAL_TEXT_SAMPLE)
    assert metrics["pickup_24h_rate"] == 99.43
    assert metrics["pickup_48h_rate"] == 100.00
    assert metrics["door_delivery_rate"] == 92.59
    assert metrics["next_day_delivery_rate"] == 53.97
    assert metrics["four_cp_rate"] == 99.47
    assert metrics["four_cp_rate_ex_remote"] == 99.46
    # 表达签准率 / 支签时长 不在日常考核默认卡片 -> None
    assert metrics["delivery_promise_rate"] is None
    assert metrics["avg_pay_to_sign_hours"] is None
    # 异常单据 3 > 0 -> 需反馈
    assert metrics["exception_feedback_required"] is True
    assert weekly == "B"


def test_extract_metrics_from_page_text_refreshes_once() -> None:
    blobs = iter(
        [
            "考核表现 数据概览 24H支揽率 48H支揽率 送货上门率 隔日达率",
            REAL_TEXT_SAMPLE,
        ]
    )
    calls = {"activate": 0, "wait": 0, "refresh": 0}

    def get_blob() -> str:
        return next(blobs)

    def activate_daily_assess() -> None:
        calls["activate"] += 1

    def wait(ms: int) -> None:
        calls["wait"] += ms

    def refresh() -> None:
        calls["refresh"] += 1

    metrics, weekly = fulfillment.extract_metrics_from_page_text(
        get_blob=get_blob,
        activate_daily_assess=activate_daily_assess,
        wait=wait,
        refresh=refresh,
    )

    assert calls["refresh"] == 1
    assert metrics["pickup_24h_rate"] == 99.43
    assert metrics["next_day_delivery_rate"] == 53.97
    assert weekly == "B"


def test_extract_weekly_warning_level_variants() -> None:
    assert fulfillment.extract_weekly_warning_level("表现为A类预警") == "A"
    assert fulfillment.extract_weekly_warning_level("表现为C类警告") == "C"
    assert fulfillment.extract_weekly_warning_level("一切正常") is None


def test_parse_metrics_normalizes_and_allows_partial() -> None:
    raw = {
        "pickup_24h_rate": "99.43",
        "pickup_48h_rate": 100,
        "door_delivery_rate": 92.59,
        "next_day_delivery_rate": 53.97,
        "four_cp_rate": 99.47,
        "four_cp_rate_ex_remote": 99.46,
        # delivery_promise_rate / avg_pay_to_sign_hours 缺失
        "exception_feedback_required": 1,
    }
    metrics = fulfillment.parse_fulfillment_metrics(raw)
    assert metrics["pickup_24h_rate"] == 99.43
    assert metrics["delivery_promise_rate"] is None
    assert metrics["avg_pay_to_sign_hours"] is None
    assert metrics["exception_feedback_required"] is True


def test_parse_metrics_all_missing_raises_not_found() -> None:
    with pytest.raises(RuntimeError, match="FULFILLMENT_OVERVIEW_NOT_FOUND"):
        fulfillment.parse_fulfillment_metrics({"exception_feedback_required": False})


def test_real_read_propagates_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        fulfillment,
        "_read_fulfillment_overview",
        lambda: (_ for _ in ()).throw(RuntimeError("FULFILLMENT_OVERVIEW_NOT_FOUND：未找到")),
    )
    with pytest.raises(RuntimeError, match="FULFILLMENT_OVERVIEW_NOT_FOUND"):
        fulfillment.run_fulfillment_overview(dry_run=False)


def test_real_read_success_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        fulfillment,
        "_read_fulfillment_overview",
        lambda: fulfillment.extract_metrics_from_text(REAL_TEXT_SAMPLE),
    )
    response = fulfillment.run_fulfillment_overview(dry_run=False)
    assert response.success is True
    assert response.data["source"] == "page"
    assert response.data["simulated"] is False
    assert response.data["weekly_warning_level"] == "B"
    assert response.data["metrics"]["next_day_delivery_rate"] == 53.97


def test_learn_returns_page_dom_note(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = fulfillment.learn_fulfillment_overview()
    assert response.success is True
    assert response.data["mode"] == "page_dom"
    assert response.data["scene"] == "fulfillment_overview"
