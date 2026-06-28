from __future__ import annotations

from ops_cli.browser import (
    KEEPALIVE_WINDOW_NAME,
    MANAGED_WINDOW_PREFIX,
    PAGE_SNAPSHOT_TIMEOUT_MS,
    build_tab_cleanup_plan,
    cleanup_browser_tabs,
    cleanup_playwright_context,
    managed_work_page,
)


class FakePage:
    def __init__(self, url: str = "about:blank", title: str = "", window_name: str = "") -> None:
        self.url = url
        self._title = title
        self.window_name = window_name
        self.closed = False
        self.default_timeouts: list[float] = []

    def title(self) -> str:
        return self._title

    def evaluate(self, _script: str, arg: str | None = None) -> str:
        if arg is not None:
            self.window_name = arg
            return self.window_name
        return self.window_name

    def goto(self, url: str, **_kwargs: object) -> None:
        self.url = url

    def is_closed(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True

    def set_default_timeout(self, timeout: float) -> None:
        self.default_timeouts.append(timeout)


class FakeContext:
    def __init__(self, pages: list[FakePage] | None = None) -> None:
        self.pages = pages or []

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page


def test_cleanup_plan_closes_managed_residue_duplicates_and_extra_blanks() -> None:
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "about:blank", "title": "about:blank", "window_name": KEEPALIVE_WINDOW_NAME},
            {"index": 1, "url": "https://web.txcs.tmall.com/pages/chaoshi/inventory", "title": "库存", "window_name": ""},
            {"index": 2, "url": "https://web.txcs.tmall.com/pages/chaoshi/inventory", "title": "库存", "window_name": ""},
            {"index": 3, "url": "https://www.erp321.com/app/order/order/list.aspx", "title": "订单", "window_name": ""},
            {"index": 4, "url": "about:blank", "title": "about:blank", "window_name": ""},
            {"index": 5, "url": "https://www.erp321.com/app/order/order/list.aspx", "title": "订单", "window_name": f"{MANAGED_WINDOW_PREFIX}jst.stats:abc"},
        ]
    )

    assert [item["index"] for item in plan["close"]] == [2, 4, 5]
    assert [item["reason"] for item in plan["close"]] == ["duplicate_url", "extra_blank", "managed_residue"]
    assert [item["index"] for item in plan["keep"]] == [0, 1, 3]


def test_cleanup_plan_keeps_recent_managed_marker() -> None:
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "about:blank", "title": "", "window_name": KEEPALIVE_WINDOW_NAME},
            {"index": 1, "url": "https://www.erp321.com/app/order/order/list.aspx", "title": "订单", "window_name": f"{MANAGED_WINDOW_PREFIX}jst.stats:1000:abcd1234"},
        ],
        now=1100,
        managed_residue_min_age_seconds=300,
    )

    assert [item["index"] for item in plan["keep"]] == [0, 1]
    assert plan["close"] == []


def test_cleanup_plan_closes_old_managed_marker() -> None:
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "about:blank", "title": "", "window_name": KEEPALIVE_WINDOW_NAME},
            {"index": 1, "url": "https://www.erp321.com/app/order/order/list.aspx", "title": "订单", "window_name": f"{MANAGED_WINDOW_PREFIX}jst.stats:700:abcd1234"},
        ],
        now=1100,
        managed_residue_min_age_seconds=300,
    )

    assert [item["index"] for item in plan["close"]] == [1]
    assert plan["close"][0]["reason"] == "managed_residue"


def test_cleanup_plan_closes_unparseable_managed_marker() -> None:
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "about:blank", "title": "", "window_name": KEEPALIVE_WINDOW_NAME},
            {"index": 1, "url": "https://www.erp321.com/app/order/order/list.aspx", "title": "订单", "window_name": f"{MANAGED_WINDOW_PREFIX}jst.stats:abc"},
        ],
        now=1100,
        managed_residue_min_age_seconds=300,
    )

    assert [item["index"] for item in plan["close"]] == [1]
    assert plan["close"][0]["reason"] == "managed_residue"


def test_cleanup_plan_keeps_recent_raw_cdp_marker() -> None:
    # JS raw-CDP 标签的规范格式 ops-cli:<owner>:<秒>:<rand8>，应被年龄保护识别。
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "about:blank", "title": "", "window_name": KEEPALIVE_WINDOW_NAME},
            {"index": 1, "url": "https://web.txcs.tmall.com/inventory", "title": "库存", "window_name": f"{MANAGED_WINDOW_PREFIX}tmcs.inventory.raw:1000:ab12cd34"},
        ],
        now=1100,
        managed_residue_min_age_seconds=300,
    )

    assert [item["index"] for item in plan["keep"]] == [0, 1]
    assert plan["close"] == []


def test_cleanup_plan_handles_legacy_millisecond_marker() -> None:
    # 旧版 JS marker（毫秒、无 rand 段）也应被解析并归一到秒，近期则保留。
    ms = 1782547200000  # = 1782547200 秒
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "about:blank", "title": "", "window_name": KEEPALIVE_WINDOW_NAME},
            {"index": 1, "url": "https://web.txcs.tmall.com/inventory", "title": "库存", "window_name": f"{MANAGED_WINDOW_PREFIX}tmcs.inventory.raw:{ms}"},
        ],
        now=1782547200 + 100,
        managed_residue_min_age_seconds=300,
    )

    assert [item["index"] for item in plan["keep"]] == [0, 1]
    assert plan["close"] == []


def test_cleanup_plan_prefers_marked_keepalive_over_unmarked_blank() -> None:
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "about:blank", "title": "", "window_name": ""},
            {"index": 1, "url": "about:blank", "title": "", "window_name": KEEPALIVE_WINDOW_NAME},
        ]
    )

    assert [item["index"] for item in plan["keep"]] == [1]
    assert [item["index"] for item in plan["close"]] == [0]
    assert plan["close"][0]["reason"] == "extra_blank"


def test_cleanup_plan_does_not_treat_navigated_keepalive_as_blank() -> None:
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "https://web.txcs.tmall.com/", "title": "天猫超市", "window_name": KEEPALIVE_WINDOW_NAME},
            {"index": 1, "url": "about:blank", "title": "", "window_name": ""},
        ]
    )

    assert [item["index"] for item in plan["keep"]] == [0, 1]
    assert plan["close"] == []


def test_cleanup_plan_dedupes_known_hosts_by_normalized_path() -> None:
    plan = build_tab_cleanup_plan(
        [
            {"index": 0, "url": "about:blank", "title": "", "window_name": KEEPALIVE_WINDOW_NAME},
            {"index": 1, "url": "https://web.txcs.tmall.com/pages/chaoshi/inventory?a=1#top", "title": "库存", "window_name": ""},
            {"index": 2, "url": "https://web.txcs.tmall.com/pages/chaoshi/inventory?a=2#other", "title": "库存", "window_name": ""},
        ]
    )

    assert [item["index"] for item in plan["close"]] == [2]
    assert plan["close"][0]["reason"] == "duplicate_url"


def test_cleanup_playwright_context_dry_run_does_not_close_pages() -> None:
    duplicate = FakePage("https://web.txcs.tmall.com/pages/chaoshi/inventory", "库存")
    context = FakeContext(
        [
            FakePage("about:blank", "about:blank", KEEPALIVE_WINDOW_NAME),
            FakePage("https://web.txcs.tmall.com/pages/chaoshi/inventory", "库存"),
            duplicate,
            FakePage("https://www.erp321.com/app/order/order/list.aspx", "订单", f"{MANAGED_WINDOW_PREFIX}jst.stats:abc"),
        ]
    )

    result = cleanup_playwright_context(context, dry_run=True)

    assert result["close_count"] == 2
    assert duplicate.closed is False
    assert all(page.closed is False for page in context.pages)


def test_cleanup_playwright_context_accepts_custom_managed_age() -> None:
    managed = FakePage("https://www.erp321.com/app/order/order/list.aspx", "订单", f"{MANAGED_WINDOW_PREFIX}jst.stats:1000:abcd1234")
    context = FakeContext(
        [
            FakePage("about:blank", "about:blank", KEEPALIVE_WINDOW_NAME),
            managed,
        ]
    )

    result = cleanup_playwright_context(context, now=1100, managed_residue_min_age_seconds=300)

    assert result["close_count"] == 0
    assert managed.closed is False


def test_cleanup_browser_tabs_accepts_custom_managed_age(monkeypatch) -> None:
    managed = FakePage("https://www.erp321.com/app/order/order/list.aspx", "订单", f"{MANAGED_WINDOW_PREFIX}jst.stats:1000:abcd1234")
    context = FakeContext(
        [
            FakePage("about:blank", "about:blank", KEEPALIVE_WINDOW_NAME),
            managed,
        ]
    )
    monkeypatch.setattr("ops_cli.browser._with_cdp_context", lambda _port, handler: handler(context))

    response = cleanup_browser_tabs(9222, managed_residue_min_age_seconds=300, now=1100)

    assert response.success is True
    assert response.data["close_count"] == 0
    assert managed.closed is False


def test_cleanup_playwright_context_closes_planned_pages() -> None:
    duplicate = FakePage("https://web.txcs.tmall.com/pages/chaoshi/inventory", "库存")
    managed = FakePage("https://www.erp321.com/app/order/order/list.aspx", "订单", f"{MANAGED_WINDOW_PREFIX}jst.stats:abc")
    context = FakeContext(
        [
            FakePage("about:blank", "about:blank", KEEPALIVE_WINDOW_NAME),
            FakePage("https://web.txcs.tmall.com/pages/chaoshi/inventory", "库存"),
            duplicate,
            managed,
        ]
    )

    result = cleanup_playwright_context(context)

    assert result["close_count"] == 2
    assert duplicate.closed is True
    assert managed.closed is True


def test_snapshot_uses_short_default_timeout_for_title_and_window_name() -> None:
    page = FakePage("about:blank", "about:blank", KEEPALIVE_WINDOW_NAME)
    context = FakeContext([page])

    cleanup_playwright_context(context, dry_run=True)

    assert PAGE_SNAPSHOT_TIMEOUT_MS in page.default_timeouts
    assert page.default_timeouts[-1] == 30000


def test_managed_work_page_sets_marker_and_closes_only_work_page() -> None:
    context = FakeContext()

    with managed_work_page(context, "tmcs.inventory") as page:
        assert page.window_name.startswith(f"{MANAGED_WINDOW_PREFIX}tmcs.inventory:")
        assert page.closed is False

    keepalive, work_page = context.pages
    assert keepalive.window_name == KEEPALIVE_WINDOW_NAME
    assert keepalive.closed is False
    assert work_page.closed is True
