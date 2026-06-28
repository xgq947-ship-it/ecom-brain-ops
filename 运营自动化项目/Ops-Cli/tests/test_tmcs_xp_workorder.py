from __future__ import annotations

import sys
import types

import pytest

from ops_cli.capabilities import capability_ids, get_capability
from ops_cli.cli import app  # noqa: F401
from ops_cli.platforms.tmcs import xp_workorder


def test_xp_workorder_capability_registered() -> None:
    registered = capability_ids()
    assert "tmcs.xp-workorder.count" in registered
    assert "tmcs.xp-workorder.learn" in registered

    spec = get_capability("tmcs.xp-workorder.count")
    assert spec.platform == "tmcs"
    assert spec.command == "xp-workorder count"
    assert "xp_workorder_count" in spec.scenes


def test_extract_count_from_homepage_text() -> None:
    text = """
    基础待办
    违规处罚 1
    XP工单处理 紧急(4) 4
    小二任务 2
    """
    assert xp_workorder.extract_workorder_count(text) == 4


def test_extract_count_from_homepage_text_missing_returns_none() -> None:
    assert xp_workorder.extract_workorder_count("近1年(81)") is None


def test_detect_tmcs_login_page() -> None:
    assert xp_workorder._is_login_page("https://login.taobao.com/member/login.jhtml") is True
    assert xp_workorder._is_login_page("https://web.txcs.tmall.com/") is False


def test_count_dry_run_returns_simulated(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = xp_workorder.count_xp_workorders(threshold=4, dry_run=True)

    assert response.success is True
    assert response.platform == "tmcs"
    assert response.command == "xp-workorder count"
    data = response.data
    assert data["count"] == 0
    assert data["threshold"] == 4
    assert data["exceeded"] is False
    assert data["source"] == "simulated"
    assert data["simulated"] is True
    assert data["dry_run"] is True
    assert data["scene"].endswith("/xp_workorder_count")
    assert data["context_path"].endswith(".json")


def test_count_reads_homepage_and_returns_exceeded(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(xp_workorder, "_read_homepage_text", lambda: "XP工单处理 紧急(5)")

    response = xp_workorder.count_xp_workorders(threshold=4, dry_run=False)

    assert response.success is True
    data = response.data
    assert data["count"] == 5
    assert data["threshold"] == 4
    assert data["exceeded"] is True
    assert data["source"] == "dom"
    assert data["simulated"] is False
    assert data["dry_run"] is False


def test_count_below_threshold_not_exceeded(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(xp_workorder, "_read_homepage_text", lambda: "XP工单处理 紧急(3)")

    response = xp_workorder.count_xp_workorders(threshold=4, dry_run=False)
    assert response.data["count"] == 3
    assert response.data["exceeded"] is False


def test_count_missing_field_raises_workorder_not_found(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(xp_workorder, "_read_homepage_text", lambda: "近1年(81)")

    with pytest.raises(RuntimeError, match="WORKORDER_COUNT_NOT_FOUND"):
        xp_workorder.count_xp_workorders(threshold=4, dry_run=False)


@pytest.mark.parametrize(
    ("foreground_allowed", "expected_headless"),
    [
        (False, True),
        (True, False),
    ],
)
def test_homepage_reader_uses_headless_only_for_background_reads(
    tmp_path,
    monkeypatch,
    foreground_allowed: bool,
    expected_headless: bool,
) -> None:
    start_calls: list[bool] = []

    scene_pkg = types.ModuleType("scene")
    chrome_cdp = types.ModuleType("scene.chrome_cdp")
    chrome_cdp.CDP_URL = "http://127.0.0.1:9222"
    chrome_cdp.foreground_allowed = lambda: foreground_allowed
    chrome_cdp.bring_chrome_to_front = lambda: (True, "ok")

    def fake_start_chrome(*, headless: bool = False, **_kwargs):
        start_calls.append(headless)
        return True, "ok"

    chrome_cdp.start_chrome = fake_start_chrome
    monkeypatch.setitem(sys.modules, "scene", scene_pkg)
    monkeypatch.setitem(sys.modules, "scene.chrome_cdp", chrome_cdp)
    monkeypatch.setattr(xp_workorder, "_sessionhub_root", lambda: tmp_path)

    class FakeLocator:
        def inner_text(self, timeout: int) -> str:
            return "基础待办 XP工单处理 紧急(2)"

    class FakePage:
        url = "https://web.txcs.tmall.com/"

        def goto(self, url: str, **_kwargs) -> None:
            self.url = url

        def locator(self, _selector: str) -> FakeLocator:
            return FakeLocator()

        def wait_for_timeout(self, _ms: int) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeContext:
        pages: list[FakePage] = []

        def new_page(self) -> FakePage:
            return FakePage()

    class FakeBrowser:
        contexts = [FakeContext()]

    class FakeChromium:
        def connect_over_cdp(self, _url: str) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = Exception
    sync_api.sync_playwright = lambda: FakePlaywright()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    assert xp_workorder._read_homepage_text() == "基础待办 XP工单处理 紧急(2)"
    assert start_calls == [expected_headless]


def test_learn_is_noop_homepage_dom(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "runtime" / "context").mkdir(parents=True, exist_ok=True)

    response = xp_workorder.learn_xp_workorder_count()

    assert response.success is True
    assert response.data["mode"] == "homepage_dom"
