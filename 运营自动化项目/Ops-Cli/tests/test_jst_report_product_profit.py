"""Tests for `ops jst report product-profit export` capability."""
from __future__ import annotations

from pathlib import Path

import pytest

from ops_cli.capabilities import capability_ids
from ops_cli.cli import app  # noqa: F401 - 触发平台能力注册
from ops_cli.platforms.jst import report


def test_capability_registered() -> None:
    assert "jst.report.product-profit.export" in capability_ids()


def test_dry_run_does_not_export(monkeypatch, tmp_path: Path) -> None:
    # 即使下载目录里有文件，dry-run 也绝不消费/导出
    (tmp_path / "商品销售情况.csv").write_text("x", encoding="utf-8")

    def _boom(*a, **k):  # noqa: ANN001
        raise AssertionError("dry-run 不应查找/拾取导出文件")

    monkeypatch.setattr(report, "_find_recent_export_csv", _boom)

    response = report.export_product_profit_csv(month="2026-05", dry_run=True, download_dir=str(tmp_path))
    assert response.success is True
    assert response.data["simulated"] is True
    assert response.data["downloaded"] is False
    assert "csv_path" not in response.data
    assert response.data["month"] == "2026-05"


def test_dry_run_accepts_explicit_date_range(monkeypatch, tmp_path: Path) -> None:
    response = report.export_product_profit_csv(
        start_date="2026-06-01",
        end_date="2026-06-15",
        dry_run=True,
        download_dir=str(tmp_path),
    )
    assert response.success is True
    assert response.data["period"] == {"begin": "2026-06-01", "end": "2026-06-15"}
    assert response.data["period_label"] == "2026-06-01_to_2026-06-15"
    assert "month" not in response.data


def test_month_default_is_last_month(monkeypatch) -> None:
    import datetime as real_datetime

    class FakeDate(real_datetime.date):
        @classmethod
        def today(cls):
            return real_datetime.date(2026, 6, 3)

    monkeypatch.setattr(report, "date", FakeDate)
    assert report._last_month() == "2026-05"

    response = report.export_product_profit_csv(dry_run=True)
    assert response.data["month"] == "2026-05"


def _fake_probe(csv_path):
    """构造一个伪 probe 返回（不触真实 9222）。"""
    from ops_cli.output import CommandResponse

    def _probe(*, shop_name=None, month=None, start_date=None, end_date=None, dest=None):
        return CommandResponse(
            success=True, platform="jst", command="report product-profit learn",
            data={"csv_path": csv_path, "download_name": "x.csv", "applied_range": {"range": "时间范围：2026-05-01 ~ 2026-05-31"}},
        )
    return _probe


def test_execute_uses_9222_download(monkeypatch, tmp_path: Path) -> None:
    # 9222 真实下载（这里 mock）拿到 csv_path 时，execute 直接用它，不走下载目录拾取
    dest = tmp_path / "out" / "result.csv"
    dest.parent.mkdir(parents=True)
    dest.write_text("店铺款式编码\n", encoding="utf-8")
    monkeypatch.setattr(report, "probe_goods_profit_export", _fake_probe(str(dest)))

    response = report.export_product_profit_csv(month="2026-05", dry_run=False, dest=str(dest))
    assert response.success is True
    assert response.data["downloaded"] is True
    assert response.data["source"] == "sessionhub_9222"
    assert response.data["csv_path"] == str(dest)


def test_execute_falls_back_to_recent_csv(monkeypatch, tmp_path: Path) -> None:
    # 9222 没拿到下载（csv_path 为空）时，兜底拾取下载目录里的导出文件
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    (download_dir / "商品销售情况.csv").write_text("店铺款式编码\n", encoding="utf-8")
    dest = tmp_path / "out" / "result.csv"
    monkeypatch.setattr(report, "probe_goods_profit_export", _fake_probe(None))

    response = report.export_product_profit_csv(
        month="2026-05", dry_run=False, dest=str(dest), download_dir=str(download_dir)
    )
    assert response.success is True
    assert response.data["downloaded"] is True
    assert Path(response.data["csv_path"]).exists()


def test_execute_without_export_raises(monkeypatch, tmp_path: Path) -> None:
    # 9222 没下载到 + 下载目录也没有现成文件 -> 报清晰错误
    monkeypatch.setattr(report, "probe_goods_profit_export", _fake_probe(None))
    with pytest.raises(RuntimeError, match="商品销售情况"):
        report.export_product_profit_csv(month="2026-05", dry_run=False, download_dir=str(tmp_path))


def test_invalid_month_raises() -> None:
    with pytest.raises(RuntimeError, match="YYYY-MM"):
        report.export_product_profit_csv(month="2026-5", dry_run=True)
