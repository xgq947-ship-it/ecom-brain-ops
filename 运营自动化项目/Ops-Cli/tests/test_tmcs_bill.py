import json
from datetime import date

import pytest

from ops_cli.capabilities import bind_capability_execution, get_capability
from ops_cli.platforms.tmcs import bill
from ops_cli.platforms.tmcs import shared as tmcs_shared


def test_tmcs_bill_last_month_query_window_extends_three_days(monkeypatch) -> None:
    monkeypatch.setattr(bill, "previous_month_range", lambda: ("2026-04-01", "2026-04-30"))

    begin, finish, query_finish = bill._normalize_dates(start=None, end=None, last_month=True)

    assert begin == date(2026, 4, 1)
    assert finish == date(2026, 4, 30)
    assert query_finish == date(2026, 5, 3)


def test_tmcs_bill_download_requires_template(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="未找到猫超账单下载模板"):
        bill.run_bill_download(last_month=True)


def test_tmcs_bill_download_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "tmcs" / "bill_download_template.json"
    template_path.write_text(
        json.dumps({"defaults": {"output_dir": str(tmp_path / "downloads")}}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})

    result = bill.run_bill_download(start="2026-05-01", end="2026-05-16", dry_run=True)

    assert result.data["dry_run"] is True
    assert result.data["bill_count"] == 0


def test_tmcs_bill_download_last_month(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "tmcs" / "bill_download_template.json"
    template_path.write_text(
        json.dumps(
            {
                "defaults": {"output_dir": str(tmp_path / "downloads")},
                "bill_list": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "GET", "url": "https://example.com/list"},
                "statement_export": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "POST", "url": "https://example.com", "post_data_form": {"_scm_token_": "x", "query": "{}"}},
                "download_query": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "POST", "url": "https://example.com/query", "post_data_json": {"parameters": [{"pageIndex": 1, "pageSize": 20}], "_scm_token_": "x"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(
        bill,
        "_list_bill_items",
        lambda **kwargs: [{"billCode": "HDB202605012026051632979767827-1603202"}],
    )
    monkeypatch.setattr(
        bill,
        "_download_bill_file",
        lambda **kwargs: tmp_path / "downloads" / "HDB202605012026051632979767827-1603202.xlsx",
    )

    result = bill.run_bill_download(last_month=True)

    assert result.data["bill_count"] == 1
    assert result.data["query_grace_days"] == 3
    assert result.data["downloaded_files"][0].endswith(".xlsx")


def test_tmcs_bill_download_without_statement_list_skips_export_scene(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    template_path = tmp_path / "data" / "tmcs" / "bill_download_template.json"
    template_path.write_text(
        json.dumps(
            {
                "defaults": {"output_dir": str(tmp_path / "downloads")},
                "bill_list": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "GET", "url": "https://example.com/list"},
                "statement_export": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "POST", "url": "https://example.com/export", "post_data_form": {"_scm_token_": "x", "query": "{}"}},
                "download_query": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "POST", "url": "https://example.com/query", "post_data_json": {"parameters": [{"pageIndex": 1, "pageSize": 20}]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    checked: list[str] = []

    def fake_check(_site, scene, **_kwargs):
        checked.append(scene)
        if scene == "statement_bill_dynamic_list":
            raise RuntimeError("stale unused export scene")
        return {"status": "valid"}

    monkeypatch.setattr(bill, "check_scene_or_fail", fake_check)
    monkeypatch.setattr(bill, "_list_bill_items", lambda **kwargs: [])
    spec = get_capability("tmcs.bill.download")

    with bind_capability_execution(spec, interactive_login=False):
        result = bill.run_bill_download(last_month=True)

    assert result.data["bill_count"] == 0
    assert "statement_bill_dynamic_list" not in checked


def test_tmcs_bill_download_statement_list(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "tmcs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "tmcs" / "bill_download_template.json"
    template_path.write_text(
        json.dumps(
            {
                "defaults": {"output_dir": str(tmp_path / "downloads")},
                "bill_list": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "GET", "url": "https://example.com/list"},
                "statement_export": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "POST", "url": "https://example.com", "post_data_form": {"_scm_token_": "x", "query": "{}"}},
                "download_query": {"headers": {"cookie": "a=b"}, "cookies": [], "method": "POST", "url": "https://example.com/query", "post_data_json": {"parameters": [{"pageIndex": 1, "pageSize": 20}], "_scm_token_": "x"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(bill, "check_scene_or_fail", lambda *args, **kwargs: {"status": "valid"})
    monkeypatch.setattr(bill, "_list_bill_items", lambda **kwargs: [])
    monkeypatch.setattr(bill, "_download_statement_list", lambda **kwargs: tmp_path / "downloads" / "对账单列表.xlsx")

    result = bill.run_bill_download(start="2026-05-01", end="2026-05-16", download_statement_list=True)

    assert result.data["statement_list_path"].endswith("对账单列表.xlsx")


def test_download_statement_list_retries_gei_task_until_excel_ready(tmp_path, monkeypatch) -> None:
    export_scene = {
        "url": "https://example.com/export",
        "method": "POST",
        "headers": {},
        "cookies": [],
        "post_data_form": {"_scm_token_": "x", "query": "{\"pageIndex\":1}"},
    }
    query_scene = {
        "url": "https://example.com/query",
        "method": "POST",
        "headers": {},
        "cookies": [],
        "post_data_json": {"parameters": [{"pageIndex": 1, "pageSize": 20}]},
    }
    monkeypatch.setattr(bill.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bill, "tmcs_request", lambda *args, **kwargs: (200, {"data": {"taskId": "task-1"}}, b""))
    responses = iter(
        [
            (200, {"success": False, "errorCode": "00105"}, b'{"success":false}'),
            (200, None, b"PK\x03\x04excel"),
        ]
    )
    monkeypatch.setattr(bill, "tmcs_download", lambda *args, **kwargs: next(responses))

    path = bill._download_statement_list(
        headers={},
        export_scene=export_scene,
        query_scene=query_scene,
        start=date(2026, 4, 1),
        end=date(2026, 4, 30),
        output_dir=tmp_path,
    )

    assert path.name == "对账单列表.xlsx"
    assert path.read_bytes().startswith(b"PK\x03\x04")


def test_request_headers_only_keep_cookies_for_target_host() -> None:
    scene = {
        "headers": {"user-agent": "ua"},
        "cookies": [
            {"name": "hema", "value": "1", "domain": ".hemaos.com"},
            {"name": "tmall", "value": "2", "domain": ".tmall.com"},
        ],
    }

    headers = bill._request_headers(scene, "https://wdksettlement.hemaos.com/statementBill/v3/listForSupplier")

    assert headers["cookie"] == "hema=1"
    assert "tmall=2" not in headers["cookie"]


def test_ensure_scene_assets_refreshes_existing_invalid_scene(tmp_path, monkeypatch) -> None:
    scene_path = tmp_path / "statement_bill_list_for_supplier.json"
    scene_path.write_text(json.dumps({"scene": "statement_bill_list_for_supplier"}), encoding="utf-8")

    calls = {"ensure": 0}

    class FakeManager:
        def ensure_scene(self, site, scene):
            calls["ensure"] += 1
            return {"status": "valid"}

        def capture_scene(self, site, scene):
            raise AssertionError("force=False should not call capture_scene")

        def check_scene(self, site, scene):
            return {"status": "valid"}

    monkeypatch.setattr(tmcs_shared, "get_scene_manager", lambda: FakeManager())
    monkeypatch.setattr(tmcs_shared, "scene_store_path", lambda site, scene: scene_path)

    session, check, path = tmcs_shared.ensure_scene_assets(
        site="tmall_chaoshi",
        scene="statement_bill_list_for_supplier",
        force=False,
        next_command="ops tmcs auth capture",
    )

    assert calls["ensure"] == 1
    assert check["status"] == "valid"
    assert path == scene_path
    assert session["scene"] == "statement_bill_list_for_supplier"


def test_check_scene_or_fail_auto_ensures_invalid_scene(monkeypatch) -> None:
    calls = {"ensure": 0}

    class FakeManager:
        def check_scene(self, site, scene):
            if calls["ensure"] == 0:
                return {"status": "invalid", "check_result": {"reason": "401"}}
            return {"status": "valid", "check_result": {"reason": "ok"}}

        def ensure_scene(self, site, scene):
            calls["ensure"] += 1
            return {"status": "valid"}

    monkeypatch.setattr(tmcs_shared, "get_scene_manager", lambda: FakeManager())

    check = tmcs_shared.check_scene_or_fail(
        "tmall_chaoshi",
        "maochao_item_search",
        next_command="ops tmcs product learn",
    )

    assert calls["ensure"] == 1
    assert check["status"] == "valid"


def test_check_scene_or_fail_does_not_recover_during_dry_run(monkeypatch) -> None:
    calls = {"ensure": 0}

    class FakeManager:
        def check_scene(self, site, scene):
            return {"status": "invalid", "check_result": {"reason": "401"}}

        def ensure_scene(self, site, scene):
            calls["ensure"] += 1
            return {"status": "valid"}

    monkeypatch.setattr(tmcs_shared, "get_scene_manager", lambda: FakeManager())
    spec = get_capability("tmcs.bill.download")

    with bind_capability_execution(spec, dry_run=True, interactive_login=True):
        with pytest.raises(RuntimeError, match="Scene 校验失败"):
            tmcs_shared.check_scene_or_fail(
                "tmall_chaoshi",
                "statement_bill_list_for_supplier",
                next_command="ops tmcs bill download --dry-run",
            )

    assert calls["ensure"] == 0


def test_check_scene_or_fail_does_not_wait_in_noninteractive_execution(monkeypatch) -> None:
    calls = {"ensure": 0}

    class FakeManager:
        def check_scene(self, site, scene):
            return {"status": "invalid", "check_result": {"reason": "401"}}

        def ensure_scene(self, site, scene):
            calls["ensure"] += 1
            return {"status": "valid"}

    monkeypatch.setattr(tmcs_shared, "get_scene_manager", lambda: FakeManager())
    spec = get_capability("tmcs.bill.download")

    with bind_capability_execution(spec, interactive_login=False):
        with pytest.raises(RuntimeError, match="Scene 校验失败"):
            tmcs_shared.check_scene_or_fail(
                "tmall_chaoshi",
                "statement_bill_list_for_supplier",
                next_command="ops tmcs bill download",
            )

    assert calls["ensure"] == 0
