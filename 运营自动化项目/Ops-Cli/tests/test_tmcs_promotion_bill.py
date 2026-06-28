import json
from datetime import date

import pytest

from ops_cli.platforms.tmcs import promotion_bill


def _write_template(tmp_path, sources=None):
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    template_path = tmp_path / "data" / "tmcs" / "promotion_bill_template.json"
    template_path.write_text(
        json.dumps(
            {
                "defaults": {"output_dir": str(tmp_path / "downloads")},
                "sources": sources
                or {
                    "zdx": {
                        "url": "https://example.com/zdx/export",
                        "method": "POST",
                        "headers": {"cookie": "a=b"},
                        "cookies": [],
                        "post_data_json": {"start": "__START_DATE__", "end": "__END_DATE__"},
                    },
                    "wxt": {
                        "url": "https://example.com/wxt/export",
                        "method": "POST",
                        "headers": {"cookie": "a=b"},
                        "cookies": [],
                        "post_data_form": {"tradeStart": "__START_DATE__", "tradeEnd": "__END_DATE__"},
                    },
                },
                "download_query": {
                    "url": "https://example.com/query",
                    "method": "POST",
                    "headers": {"cookie": "a=b"},
                    "cookies": [],
                    "post_data_json": {"parameters": [{"pageIndex": 1, "pageSize": 20}]},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return template_path


def test_last_month_uses_natural_month_without_grace(monkeypatch) -> None:
    monkeypatch.setattr(promotion_bill, "previous_month_range", lambda: ("2026-04-01", "2026-04-30"))

    begin, finish = promotion_bill._normalize_dates(start=None, end=None, last_month=True)

    assert begin == date(2026, 4, 1)
    assert finish == date(2026, 4, 30)


def test_apply_date_placeholders_replaces_nested_values() -> None:
    payload = {
        "range": ["__START_DATE__", "__END_DATE__"],
        "query": {"start": "__START_DATE__ 00:00:00", "end": "__END_DATE__ 23:59:59"},
    }

    assert promotion_bill._apply_date_placeholders(payload, "2026-04-01", "2026-04-30") == {
        "range": ["2026-04-01", "2026-04-30"],
        "query": {"start": "2026-04-01 00:00:00", "end": "2026-04-30 23:59:59"},
    }


def test_json_task_payload_is_not_misclassified_as_csv() -> None:
    assert promotion_bill._is_probably_csv(b'{\"data\":{\"taskId\":\"GEI@x\"},\"success\":true}') is False


def test_single_line_csv_header_is_classified_as_csv() -> None:
    assert promotion_bill._is_probably_csv("记账时间,交易日期,收支类型,交易类型,操作金额(元)\n".encode("gb18030")) is True


def test_download_dry_run_lists_selected_source(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(tmp_path)
    monkeypatch.setattr(promotion_bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})

    result = promotion_bill.run_promotion_bill_download(source="zdx", start="2026-04-01", end="2026-04-30", dry_run=True)

    assert result.command == "promotion-bill download"
    assert result.data["dry_run"] is True
    assert [item["source"] for item in result.data["sources"]] == ["zdx"]
    assert result.data["start"] == "2026-04-01"
    assert result.data["end"] == "2026-04-30"


def test_download_supports_direct_file_url(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(tmp_path, sources={"zdx": {"url": "https://example.com/export", "method": "POST", "headers": {}, "cookies": [], "post_data_json": {}}})
    monkeypatch.setattr(promotion_bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(promotion_bill, "tmcs_request", lambda *args, **kwargs: (200, {"data": {"downloadUrl": "https://oss.example.com/zdx.xlsx"}}, b""))
    monkeypatch.setattr(promotion_bill, "tmcs_download", lambda *args, **kwargs: (200, None, b"PK\x03\x04direct"))

    result = promotion_bill.run_promotion_bill_download(source="zdx", start="2026-04-01", end="2026-04-30")

    assert result.data["failed"] == []
    assert result.data["downloaded_files"][0].endswith("智多星推广账单_2026-04.xlsx")


def test_download_supports_task_id(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(tmp_path, sources={"wxt": {"url": "https://example.com/export", "method": "POST", "headers": {}, "cookies": [], "post_data_form": {}}})
    monkeypatch.setattr(promotion_bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(promotion_bill, "tmcs_request", lambda *args, **kwargs: (200, {"data": {"taskId": "task-1"}}, b""))
    monkeypatch.setattr(promotion_bill, "tmcs_download", lambda *args, **kwargs: (200, None, "交易日期,收入,支出\n2026-04-01,0,0\n".encode()))

    result = promotion_bill.run_promotion_bill_download(source="wxt", start="2026-04-01", end="2026-04-30")

    assert result.data["failed"] == []
    assert result.data["downloaded_files"][0].endswith("万象台推广账单_2026-04.csv")


def test_download_retries_gei_task_until_excel_ready(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(tmp_path, sources={"zdx": {"url": "https://example.com/export", "method": "POST", "headers": {}, "cookies": [], "post_data_json": {}}})
    monkeypatch.setattr(promotion_bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(promotion_bill.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(promotion_bill, "tmcs_request", lambda *args, **kwargs: (200, {"data": {"taskId": "task-1"}}, b""))
    responses = iter(
        [
            (200, {"data": {"taskId": "task-1"}}, b'{"data":{"taskId":"task-1"},"success":true}'),
            (200, None, b"PK\x03\x04excel"),
        ]
    )
    monkeypatch.setattr(promotion_bill, "tmcs_download", lambda *args, **kwargs: next(responses))

    result = promotion_bill.run_promotion_bill_download(source="zdx", start="2026-04-01", end="2026-04-30")

    assert result.data["failed"] == []
    assert result.data["downloaded_files"][0].endswith("智多星推广账单_2026-04.xlsx")


def test_download_supports_download_center_poll(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(tmp_path, sources={"zdx": {"url": "https://example.com/export", "method": "POST", "headers": {}, "cookies": [], "post_data_json": {}}})
    monkeypatch.setattr(promotion_bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(promotion_bill.time, "sleep", lambda *_args, **_kwargs: None)

    def fake_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url")
        if str(url).endswith("/query"):
            return 200, {"data": [{"fileName": "智多星推广账单", "fileUrl": "https://oss.example.com/zdx.xlsx"}]}, b""
        return 200, {"success": True}, b""

    monkeypatch.setattr(promotion_bill, "tmcs_request", fake_request)
    monkeypatch.setattr(promotion_bill, "tmcs_download", lambda *args, **kwargs: (200, None, b"PK\x03\x04center"))

    result = promotion_bill.run_promotion_bill_download(source="zdx", start="2026-04-01", end="2026-04-30")

    assert result.data["failed"] == []
    assert result.data["downloaded_files"][0].endswith(".xlsx")


def test_download_reuses_existing_wxt_file_when_template_source_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    existing = output_dir / "万象台推广账单_2026-04.csv"
    existing.write_text("交易日期,收入,支出\n2026-04-01,0,0\n", encoding="utf-8")
    _write_template(
        tmp_path,
        sources={
            "zdx": {
                "url": "https://example.com/zdx/export",
                "method": "POST",
                "headers": {},
                "cookies": [],
                "post_data_json": {},
            }
        },
    )
    monkeypatch.setattr(promotion_bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(
        promotion_bill,
        "learn_promotion_bill",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("existing wxt file should not trigger learn")),
    )

    result = promotion_bill.run_promotion_bill_download(source="wxt", start="2026-04-01", end="2026-04-30")

    assert result.data["failed"] == []
    assert result.data["downloaded_files"] == [str(existing.resolve())]


def test_download_surfaces_business_auth_failure_before_polling(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(promotion_bill.time, "sleep", lambda *_args, **_kwargs: None)
    scene = {"url": "https://example.com/export", "method": "POST", "headers": {}, "cookies": [], "post_data_json": {}}
    query_scene = {"url": "https://example.com/query", "method": "POST", "headers": {}, "cookies": [], "post_data_json": {}}
    monkeypatch.setattr(
        promotion_bill,
        "tmcs_request",
        lambda *args, **kwargs: (
            200,
            {"success": False, "errorCode": "PL_GEI_U00001", "errorMessage": "登录会话失效，尝试重新登录"},
            b'{"success":false}',
        ),
    )

    with pytest.raises(RuntimeError, match="登录会话失效"):
        promotion_bill._download_source(
            source="zdx",
            scene=scene,
            query_scene=query_scene,
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
            output_dir=tmp_path,
        )


def test_download_fails_when_all_selected_sources_fail(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(tmp_path, sources={"zdx": {"url": "https://example.com/export", "method": "POST", "headers": {}, "cookies": [], "post_data_json": {}}})
    monkeypatch.setattr(promotion_bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(promotion_bill, "tmcs_request", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="推广账单下载全部失败"):
        promotion_bill.run_promotion_bill_download(source="zdx", start="2026-04-01", end="2026-04-30")


def test_download_relearns_wxt_after_http_403(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(tmp_path, sources={"wxt": {"url": "https://example.com/export", "method": "POST", "headers": {}, "cookies": [], "post_data_form": {}}})
    monkeypatch.setattr(promotion_bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(promotion_bill.time, "sleep", lambda *_args, **_kwargs: None)
    calls = {"learn": 0, "request": 0}

    def fake_request(*args, **kwargs):
        calls["request"] += 1
        if calls["request"] == 1:
            raise RuntimeError(
                "Client error '403 ' for url "
                "'https://stlacct.alimama.com/settleAccount/account/exportAccountJournalWithTrans.action'"
            )
        return 200, {"data": {"taskId": "task-1"}}, b""

    def fake_learn(*, source: str, force: bool, timeout: int = 90):
        calls["learn"] += 1
        assert source == "wxt"
        assert force is True

    monkeypatch.setattr(promotion_bill, "tmcs_request", fake_request)
    monkeypatch.setattr(promotion_bill, "tmcs_download", lambda *args, **kwargs: (200, None, "交易日期,收入,支出\n2026-04-01,0,0\n".encode()))
    monkeypatch.setattr(promotion_bill, "learn_promotion_bill", fake_learn)

    result = promotion_bill.run_promotion_bill_download(source="wxt", start="2026-04-01", end="2026-04-30")

    assert calls["learn"] == 1
    assert result.data["failed"] == []
    assert result.data["downloaded_files"][0].endswith("万象台推广账单_2026-04.csv")


def test_learn_single_source_merges_existing_template_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(
        tmp_path,
        sources={
            "wxt": {
                "url": "https://example.com/wxt/export",
                "method": "POST",
                "headers": {},
                "cookies": [],
                "post_data_form": {},
            }
        },
    )

    fake_scene = {
        "url": "https://example.com/zdx/export",
        "method": "POST",
        "headers": {},
        "cookies": [],
        "post_data_json": {"start": "__START_DATE__", "end": "__END_DATE__"},
    }
    monkeypatch.setattr(promotion_bill, "_capture_primary_source", lambda *args, **kwargs: fake_scene)
    monkeypatch.setattr(promotion_bill, "ensure_scene_assets", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("skip")))
    monkeypatch.setattr(promotion_bill, "load_scene_or_fail", lambda *args, **kwargs: fake_scene)

    promotion_bill.learn_promotion_bill(source="zdx")

    template = json.loads((tmp_path / "data" / "tmcs" / "promotion_bill_template.json").read_text(encoding="utf-8"))
    assert set(template["sources"]) == {"wxt", "zdx"}


def test_forced_learn_does_not_fall_back_to_stale_scene(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _write_template(tmp_path)
    stale_scene = {"url": "https://example.com/stale", "method": "POST", "headers": {}, "cookies": []}
    monkeypatch.setattr(promotion_bill, "_capture_primary_source", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        promotion_bill,
        "ensure_scene_assets",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("capture failed")),
    )
    monkeypatch.setattr(promotion_bill, "load_scene_or_fail", lambda *args, **kwargs: stale_scene)

    with pytest.raises(RuntimeError, match="capture failed"):
        promotion_bill.learn_promotion_bill(source="zdx", force=True)
