from ops_cli.integrations.sessionhub import SessionHubSceneManager
from sessionhub.scene.site_config import get_scene_config
from sessionhub.scene import token_capture


def test_progress_is_emitted_to_stderr_only(capsys) -> None:
    token_capture._progress("等待登录")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "等待登录\n"


def test_sessionhub_scene_manager_requires_root() -> None:
    manager = SessionHubSceneManager(root="/tmp/does-not-exist-sessionhub")

    try:
        manager.check_scene("jst_erp", "order_list")
    except FileNotFoundError as exc:
        assert "SessionHub" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_is_login_page_skips_non_login_home_url() -> None:
    assert token_capture._is_login_page(
        "https://www.erp321.com/app/order/order/list.aspx",
        "https://www.erp321.com/app/order/order/list.aspx",
    ) is False


def test_click_any_text_uses_first_available(monkeypatch) -> None:
    attempted: list[str] = []

    def fake_click(page, text):
        attempted.append(text)
        return text == "导出"

    monkeypatch.setattr(token_capture, "_click_by_text", fake_click)

    assert token_capture._click_any_text(object(), ["查询", "导出", "下载"]) is True
    assert attempted == ["查询", "导出"]


def test_zdx_recovery_opens_actual_export_page_and_matches_export_api() -> None:
    scene = get_scene_config("tmall_chaoshi", "tmcs_promotion_zdx_bill_export")

    assert "plan_throw_account_admin" in scene["target_url"]
    assert scene["match_url_contains"] == ["/gei/export/task/ad-funds-flow-export"]
