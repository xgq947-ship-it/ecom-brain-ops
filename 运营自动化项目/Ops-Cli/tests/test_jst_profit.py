import json

import pytest

from ops_cli.platforms.jst import profit


def test_run_yesterday_profit_requires_template(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="未找到利润统计模板"):
        profit.run_yesterday_profit()


def test_extract_profit_from_payload() -> None:
    payload = {
        "data": {
            "summaryData": {
                "dayList": [
                    {"name": "销售收入", "sumValue": "123.45"},
                    {"name": "经营利润", "sumValue": "929.80"},
                ]
            }
        }
    }

    result = profit.extract_profit_metric(payload)

    assert result == 929.8


def test_extract_profit_metrics_from_payload() -> None:
    payload = {
        "data": {
            "summaryData": {
                "dayList": [
                    {"name": "销售收入", "sumValue": "1,234.56", "id": 1},
                    {"name": "经营利润", "sumValue": "929.80", "id": 2, "percent": "30.10%"},
                    {"name": "", "sumValue": "999"},
                ]
            }
        }
    }

    result = profit.extract_profit_metrics(payload)

    assert result == [
        {"name": "销售收入", "value": 1234.56, "raw_value": "1,234.56", "id": 1},
        {"name": "经营利润", "value": 929.8, "raw_value": "929.80", "id": 2, "percent": "30.10%"},
    ]


def test_detail_payload_includes_full_raw_response() -> None:
    payload = {
        "code": 0,
        "data": {
            "summaryData": {
                "dayList": [
                    {"name": "经营利润", "sumValue": "929.80"},
                ]
            },
            "shopSummaryData": {"rows": []},
        },
    }

    result = profit._detail_payload(payload)

    assert result["raw_response"] == payload
    assert result["raw_data"] == payload["data"]
    assert result["metrics"] == [{"name": "经营利润", "value": 929.8, "raw_value": "929.80"}]


def test_run_yesterday_profit_with_template(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "profit_yesterday_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"Cookie": "a=b"},
                "post_data_json": {
                    "data": {
                        "condition": {
                            "shop": [12633507],
                            "shopNames": "（猫超）福安市启明工贸有限公司（肖国清）",
                            "dateType": "senddate",
                            "returnType": "receive_date",
                            "isCkreturnrecDateSendRtmoney": True,
                            "date": ["2026-05-14T16:00:00.000Z", "2026-05-15T15:59:59.999Z"],
                            "olderDate": ["2026-05-14T16:00:00.000Z", "2026-05-15T15:59:59.999Z"],
                            "beginDate": "2026-05-15",
                            "endDate": "2026-05-15",
                        }
                    }
                },
                "defaults": {"store": "（猫超）福安市启明工贸有限公司（肖国清）"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        status_code = 200
        text = json.dumps(
            {
                "code": 0,
                "data": {
                    "summaryData": {
                        "dayList": [
                            {"name": "经营利润", "sumValue": "929.80"},
                        ]
                    }
                },
            },
            ensure_ascii=False,
        )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, json=None):
            return FakeResponse()

    monkeypatch.setattr(profit, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(profit, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(
        json.dumps({"headers": {"Cookie": "a=b"}, "method": "POST", "url": "https://example.com"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(profit, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    result = profit.run_yesterday_profit()

    assert result.data["profit"] == 929.8
    assert result.data["metric_field"] == "经营利润"
    assert result.data["scene"] == "business_profit_multi_dimension_report"


def test_run_month_profit_with_template(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "profit_yesterday_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"Cookie": "a=b"},
                "post_data_json": {
                    "data": {
                        "condition": {
                            "shop": [12633507],
                            "shopNames": "（猫超）福安市启明工贸有限公司（肖国清）",
                            "dateType": "senddate",
                            "returnType": "receive_date",
                            "isCkreturnrecDateSendRtmoney": True,
                        }
                    }
                },
                "defaults": {"store": "（猫超）福安市启明工贸有限公司（肖国清）"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    captured_request: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = json.dumps(
            {
                "code": 0,
                "data": {
                    "summaryData": {
                        "dayList": [
                            {"name": "经营利润", "sumValue": "45678.90"},
                        ]
                    }
                },
            },
            ensure_ascii=False,
        )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, json=None):
            captured_request.update({"method": method, "url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(profit, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(profit, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(
        json.dumps({"headers": {"Cookie": "a=b"}, "method": "POST", "url": "https://example.com"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(profit, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    result = profit.get_month_profit(month="2026-04")

    assert result.data["month"] == "2026-04"
    assert result.data["profit"] == 45678.9
    assert result.data["metric_field"] == "经营利润"
    assert captured_request["json"]["data"]["condition"]["beginDate"] == "2026-04-01"
    assert captured_request["json"]["data"]["condition"]["endDate"] == "2026-04-30"
    assert captured_request["json"]["data"]["condition"]["date"] == ["2026-03-31T16:00:00.000Z", "2026-04-30T15:59:59.999Z"]
    assert captured_request["json"]["data"]["condition"]["olderDate"] == ["2026-03-31T16:00:00.000Z", "2026-04-30T15:59:59.999Z"]


def test_get_month_profit_rejects_invalid_month() -> None:
    with pytest.raises(RuntimeError, match="月份只支持 YYYY-MM"):
        profit.get_month_profit(month="2026/04")


def test_apply_month_payload_overrides_caps_current_month_to_yesterday(monkeypatch) -> None:
    monkeypatch.setattr(profit, "_today", lambda: __import__("datetime").date(2026, 5, 31))
    template = {
        "post_data_json": {"data": {"condition": {"shop": [12633507]}}},
        "defaults": {"store": "（猫超）福安市启明工贸有限公司（肖国清）"},
    }

    payload = profit._apply_month_payload_overrides(
        template,
        month="2026-05",
        store="（猫超）福安市启明工贸有限公司（肖国清）",
    )

    condition = payload["data"]["condition"]
    assert condition["beginDate"] == "2026-05-01"
    assert condition["endDate"] == "2026-05-30"
    assert condition["date"] == ["2026-04-30T16:00:00.000Z", "2026-05-30T15:59:59.999Z"]


def test_overrides_force_finance_fee_scheme_rule_id() -> None:
    store = "（猫超）福安市启明工贸有限公司（肖国清）"
    # 模板里残留的是非财务方案 ruleId，override 必须把它强制改成财务 2328
    template = {
        "post_data_json": {"data": {"condition": {"shop": [12633507], "ruleId": 16939}}},
        "defaults": {"store": store},
    }

    daily = profit._apply_payload_overrides(
        template, target_date=__import__("datetime").date(2026, 5, 20), store=store
    )
    assert daily["data"]["condition"]["ruleId"] == profit.PROFIT_FEE_RULE_ID == 2328


def test_overrides_respect_defaults_fee_rule_id() -> None:
    store = "（猫超）福安市启明工贸有限公司（肖国清）"
    template = {
        "post_data_json": {"data": {"condition": {"shop": [12633507], "ruleId": 1}}},
        "defaults": {"store": store, "fee_rule_id": 999},
    }

    daily = profit._apply_payload_overrides(
        template, target_date=__import__("datetime").date(2026, 5, 20), store=store
    )
    assert daily["data"]["condition"]["ruleId"] == 999


def test_apply_month_payload_overrides_rejects_current_month_on_first_day(monkeypatch) -> None:
    monkeypatch.setattr(profit, "_today", lambda: __import__("datetime").date(2026, 5, 1))
    template = {
        "post_data_json": {"data": {"condition": {"shop": [12633507]}}},
        "defaults": {"store": "（猫超）福安市启明工贸有限公司（肖国清）"},
    }

    with pytest.raises(RuntimeError, match="当月利润需次日才能查询"):
        profit._apply_month_payload_overrides(
            template,
            month="2026-05",
            store="（猫超）福安市启明工贸有限公司（肖国清）",
        )


def test_profit_uses_extended_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "profit_yesterday_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"Cookie": "a=b"},
                "post_data_json": {"data": {"condition": {"shop": [12633507]}}},
                "defaults": {"store": "（猫超）福安市启明工贸有限公司（肖国清）"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    captured_kwargs: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        text = json.dumps(
            {"data": {"summaryData": {"dayList": [{"name": "经营利润", "sumValue": "1.00"}]}}},
            ensure_ascii=False,
        )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, json=None):
            return FakeResponse()

    def fake_build_client(**kwargs):
        captured_kwargs.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(profit, "build_client", fake_build_client)
    monkeypatch.setattr(profit, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(
        json.dumps({"headers": {"Cookie": "a=b"}, "method": "POST", "url": "https://example.com"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(profit, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    profit.run_yesterday_profit()

    assert captured_kwargs["timeout"] == profit.PROFIT_REQUEST_TIMEOUT


def test_profit_injects_scene_cookies_when_template_is_sanitized(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "profit_yesterday_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"accept": "application/json"},
                "post_data_json": {"data": {"condition": {"shop": [12633507]}}},
                "defaults": {"store": "（猫超）福安市启明工贸有限公司（肖国清）"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    seen_headers: dict[str, str] = {}

    class FakeResponse:
        status_code = 200
        text = json.dumps(
            {"data": {"summaryData": {"dayList": [{"name": "经营利润", "sumValue": "1.00"}]}}},
            ensure_ascii=False,
        )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, json=None):
            seen_headers.update(headers or {})
            return FakeResponse()

    monkeypatch.setattr(profit, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(profit, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(
        json.dumps(
            {
                "headers": {},
                "method": "POST",
                "url": "https://example.com",
                "cookies": [{"name": "sid", "value": "abc"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(profit, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    profit.run_yesterday_profit()

    assert seen_headers["cookie"] == "sid=abc"


def test_parse_day_value_accepts_iso_today_yesterday(monkeypatch) -> None:
    import datetime

    monkeypatch.setattr(profit, "_today", lambda: datetime.date(2026, 6, 23))
    assert profit._parse_day_value("2026-06-15") == datetime.date(2026, 6, 15)
    assert profit._parse_day_value("today") == datetime.date(2026, 6, 23)
    assert profit._parse_day_value("yesterday") == datetime.date(2026, 6, 22)
    assert profit._parse_day_value("") == datetime.date(2026, 6, 22)
    with pytest.raises(RuntimeError):
        profit._parse_day_value("2026/06/15")


def test_run_day_profit_uses_requested_date(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "profit_yesterday_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"Cookie": "a=b"},
                "post_data_json": {
                    "data": {
                        "condition": {
                            "shop": [12633507],
                            "shopNames": "（猫超）福安市启明工贸有限公司（肖国清）",
                            "dateType": "senddate",
                            "returnType": "receive_date",
                            "isCkreturnrecDateSendRtmoney": True,
                            "date": ["2026-05-14T16:00:00.000Z", "2026-05-15T15:59:59.999Z"],
                            "olderDate": ["2026-05-14T16:00:00.000Z", "2026-05-15T15:59:59.999Z"],
                            "beginDate": "2026-05-15",
                            "endDate": "2026-05-15",
                        }
                    }
                },
                "defaults": {"store": "（猫超）福安市启明工贸有限公司（肖国清）"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    seen = {}

    class FakeResponse:
        status_code = 200
        text = json.dumps(
            {"code": 0, "data": {"summaryData": {"dayList": [{"name": "经营利润", "sumValue": "123.45"}]}}},
            ensure_ascii=False,
        )

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, json=None):
            seen["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(profit, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(profit, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(
        json.dumps({"headers": {"Cookie": "a=b"}, "method": "POST", "url": "https://example.com"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(profit, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    result = profit.run_day_profit(date_value="2026-06-15")

    assert result.command == "profit day"
    assert result.data["date"] == "2026-06-15"
    assert result.data["profit"] == 123.45
    condition = seen["payload"]["data"]["condition"]
    assert condition["beginDate"] == "2026-06-15"
    assert condition["endDate"] == "2026-06-15"
