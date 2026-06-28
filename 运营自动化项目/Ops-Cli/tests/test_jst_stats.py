import json
from pathlib import Path

import pytest

from ops_cli.platforms.jst import stats


def test_run_order_stats_requires_template(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="未找到订单统计模板"):
        stats.run_order_stats()


def test_run_order_stats_with_template(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "order_stats_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"Cookie": "a=b"},
                "post_data_form": {"__CALLBACKPARAM": "{}"},
                "callback_payload": {"Method": "LoadDataToJSON", "Args": ["1", "[]", "{}"]},
                "metadata": {"captured_for_date": "2026-05-16"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        status_code = 200
        text = '{"rows":[{"已付款金额":"100.50"},{"已付款金额":"200"}]}'

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, data=None):
            return FakeResponse()

    monkeypatch.setattr(stats, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(stats, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(json.dumps({"headers": {"Cookie": "a=b"}, "method": "POST", "url": "https://example.com"}), encoding="utf-8")
    monkeypatch.setattr(stats, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    result = stats.run_order_stats()

    assert result.data["order_count"] == 2
    assert result.data["paid_amount"] == 300.5
    assert result.data["scene"] == "profit_multi_dimension_report"


def test_run_order_stats_fetches_until_page_is_not_full(monkeypatch, tmp_path) -> None:
    from urllib.parse import parse_qs

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "order_stats_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"Cookie": "a=b"},
                "post_data_form": {"__CALLBACKPARAM": "{}"},
                "callback_payload": {
                    "Method": "LoadDataToJSON",
                    "Args": ["1", "[]", json.dumps({"pageSize": 2}, separators=(",", ":"))],
                },
                "metadata": {"captured_for_date": "2026-05-16"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        status_code = 200

        def __init__(self, text: str):
            self.text = text

    requested_pages: list[str] = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, data=None):
            callback_raw = parse_qs(data)["__CALLBACKPARAM"][0]
            page = str(json.loads(callback_raw)["Args"][0])
            requested_pages.append(page)
            page_rows = {
                "1": [{"已付款金额": "100"}, {"已付款金额": "200"}],
                "2": [{"已付款金额": "300"}],
            }[page]
            return FakeResponse(json.dumps({"rows": page_rows}, ensure_ascii=False))

    monkeypatch.setattr(stats, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(stats, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(
        json.dumps({"headers": {"Cookie": "a=b"}, "method": "POST", "url": "https://example.com"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(stats, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    result = stats.run_order_stats()

    assert requested_pages == ["1", "2"]
    assert result.data["order_count"] == 3
    assert result.data["paid_amount"] == 600.0


def test_run_order_stats_retries_once_when_response_body_is_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "order_stats_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"Cookie": "a=b"},
                "post_data_form": {"__CALLBACKPARAM": "{}"},
                "callback_payload": {"Method": "LoadDataToJSON", "Args": ["1", "[]", "{}"]},
                "metadata": {"captured_for_date": "2026-05-16"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        status_code = 200

        def __init__(self, text: str):
            self.text = text
            self.headers = {"content-length": str(len(text))}

    attempts: list[int] = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, data=None):
            attempts.append(1)
            if len(attempts) == 1:
                return FakeResponse("")
            return FakeResponse('{"rows":[{"已付款金额":"88.00"}]}')

    monkeypatch.setattr(stats, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(stats, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(
        json.dumps({"headers": {"Cookie": "a=b"}, "method": "POST", "url": "https://example.com"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(stats, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    result = stats.run_order_stats()

    assert len(attempts) == 2
    assert result.data["order_count"] == 1
    assert result.data["paid_amount"] == 88.0


def test_run_order_stats_tolerates_two_empty_response_bodies(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "jst").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    template_path = tmp_path / "data" / "jst" / "order_stats_template.json"
    template_path.write_text(
        json.dumps(
            {
                "method": "POST",
                "url": "https://example.com",
                "headers": {"Cookie": "a=b"},
                "post_data_form": {"__CALLBACKPARAM": "{}"},
                "callback_payload": {"Method": "LoadDataToJSON", "Args": ["1", "[]", "{}"]},
                "metadata": {"captured_for_date": "2026-05-16"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        status_code = 200

        def __init__(self, text: str):
            self.text = text
            self.headers = {"content-length": str(len(text))}

    attempts: list[int] = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, url, headers=None, data=None):
            attempts.append(1)
            if len(attempts) < 3:
                return FakeResponse("")
            return FakeResponse('{"rows":[{"已付款金额":"165.05"}]}')

    monkeypatch.setattr(stats, "build_client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(stats, "_scene_store_path", lambda site, scene: tmp_path / "scene.json")
    (tmp_path / "scene.json").write_text(
        json.dumps({"headers": {"Cookie": "a=b"}, "method": "POST", "url": "https://example.com"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(stats, "_scene_is_valid", lambda scene_data: {"valid": True, "reason": "ok"})

    result = stats.run_order_stats()

    assert len(attempts) == 3
    assert result.data["order_count"] == 1
    assert result.data["paid_amount"] == 165.05


def test_empty_response_parse_error_includes_response_diagnostics() -> None:
    class FakeResponse:
        status_code = 204
        text = ""
        headers = {"content-length": "0", "x-request-id": "req-1"}

    with pytest.raises(stats.ResponseParseError) as exc_info:
        stats._extract_json_payload(FakeResponse())

    assert exc_info.value.response_diagnostics == {
        "status_code": 204,
        "content_length": 0,
        "response_preview": "",
    }


def test_infer_template_metadata_uses_order_date_filter_keys() -> None:
    metadata = stats._infer_template_metadata(
        filters=[
            {"k": "status", "v": "waitconfirm", "c": "@="},
            {"k": "order_date", "v": "2026-06-16", "c": ">=", "t": "date"},
            {"k": "order_date", "v": "2026-06-16 23:59:59.998", "c": "<=", "t": "date"},
        ],
        captured_date=stats.date(2026, 6, 18),
        default_store="（猫超）福安市启明工贸有限公司（肖国清）",
    )

    assert metadata["date_filter_indices"] == [1, 2]


def test_apply_template_overrides_recovers_order_date_indices_when_metadata_is_empty() -> None:
    from urllib.parse import parse_qsl

    template = {
        "headers": {"Cookie": "a=b"},
        "post_data_form": {"__CALLBACKID": "JTable1", "__CALLBACKPARAM": "{}"},
        "callback_payload": {
            "Method": "LoadDataToJSON",
            "Args": [
                "1",
                json.dumps(
                    [
                        {"k": "shop_id", "v": "12633507", "c": "@="},
                        {"k": "order_date", "v": "2026-06-16", "c": ">=", "t": "date"},
                        {"k": "order_date", "v": "2026-06-16 23:59:59.998", "c": "<=", "t": "date"},
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "{}",
            ],
        },
        "metadata": {"captured_for_date": "2026-06-16", "date_filter_indices": []},
    }

    _, encoded, _ = stats._apply_template_overrides(
        template,
        date_value=stats.date(2026, 6, 18),
        store="（猫超）福安市启明工贸有限公司（肖国清）",
        shop_id="12633507",
    )

    decoded = dict(parse_qsl(encoded))
    callback_payload = json.loads(decoded["__CALLBACKPARAM"])
    filters = json.loads(callback_payload["Args"][1])
    assert filters[1]["v"] == "2026-06-18"
    assert filters[2]["v"] == "2026-06-18 23:59:59.998"


def test_apply_template_overrides_url_encodes_form_body() -> None:
    """回归测试：表单体必须 URL 编码。

    __CALLBACKPARAM 的值是含 {}":,[] 和空格、且可能含 & 的 JSON，
    若不编码直接拼成 body，服务器(application/x-www-form-urlencoded)解析失败返回空响应。
    见 stats.py _apply_template_overrides。
    """
    from urllib.parse import parse_qsl

    template = {
        "method": "POST",
        "url": "https://example.com",
        "headers": {"Cookie": "a=b"},
        "post_data_form": {"__CALLBACKID": "ACall1", "__CALLBACKPARAM": "{}"},
        # 值里故意放入会破坏裸拼接的字符：空格、& 、引号、日期范围
        "callback_payload": {
            "Method": "LoadDataToJSON",
            "Args": ["1", '[{"k":"shop","v":"启明 & 工贸","c":"=="},{"k":"d","v":"2026-06-07 23:59:59.998","c":"<="}]', "{}"],
        },
        "metadata": {"captured_for_date": "2026-06-07"},
    }

    from datetime import date as _date

    headers, encoded, callback_payload = stats._apply_template_overrides(
        template, date_value=_date(2026, 6, 7), store="启明 & 工贸"
    )

    # 1. body 里不得出现未编码的危险裸字符
    assert "{" not in encoded and "}" not in encoded
    assert '"' not in encoded
    assert " " not in encoded  # 空格必须被编码

    # 2. 解码后能无损还原出 __CALLBACKPARAM 原始 JSON（含特殊字符）
    decoded = dict(parse_qsl(encoded))
    assert decoded["__CALLBACKID"] == "ACall1"
    assert json.loads(decoded["__CALLBACKPARAM"]) == json.loads(callback_payload)
    # 值里的 & 不会被误当作字段分隔符
    assert "启明 & 工贸" in decoded["__CALLBACKPARAM"]


def test_apply_template_overrides_uses_template_cookies() -> None:
    template = {
        "method": "POST",
        "url": "https://example.com",
        "headers": {"accept": "*/*"},
        "cookies": [{"name": "sid", "value": "abc"}],
        "post_data_form": {"__CALLBACKID": "ACall1", "__CALLBACKPARAM": "{}"},
        "callback_payload": {"Method": "LoadDataToJSON", "Args": ["1", "[]", "{}"]},
        "metadata": {"captured_for_date": "2026-06-07"},
    }

    headers, _, _ = stats._apply_template_overrides(
        template,
        date_value=stats.date(2026, 6, 7),
        store="（猫超）福安市启明工贸有限公司（肖国清）",
    )

    assert headers["cookie"] == "sid=abc"


def test_extract_json_payload_supports_wrapped_response() -> None:
    payload = stats._extract_json_payload(
        '0|{"IsSuccess":true,"ReturnValue":"{\\"datas\\":[{\\"已付款金额\\":\\"123.45\\"}]}"}'
    )

    assert payload["datas"][0]["已付款金额"] == "123.45"
