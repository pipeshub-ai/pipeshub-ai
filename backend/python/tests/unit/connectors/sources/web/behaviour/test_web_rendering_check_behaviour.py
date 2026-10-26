"""Deciding whether a site needs the browser (scenarios RX-01, RX-02, RX-05, RX-06; audit FETCH-04, ORCH-11, ORCH-12)."""

from web_behaviour_fakes import (
    START_URL,
    FakeCheckpointStore,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    html_page,
)

SHELL = b"<html><head><title>App</title></head><body><div id='root'></div><script defer src='/app.js'></script></body></html>"
LONG_TEXT = "This paragraph only exists once the app's scripts have run. " * 5
INSIDE = "http://site.test/inside"


def _spa(site: FakeWeb, *, snapshot_sees_content: bool) -> None:
    rendered = html_page("App", "/inside", text=LONG_TEXT)
    # A deferred bundle has already rendered by DOMContentLoaded, so the in-browser "before"
    # snapshot sees the content too; only the HTML the server sent tells the truth.
    pre = len(LONG_TEXT) if snapshot_sees_content else 0
    site.add(START_URL, Page(body=SHELL, rendered=rendered, pre_render_text_len=pre))
    site.add(INSIDE, Page(body=SHELL, rendered=html_page("Inside", text=LONG_TEXT)))


async def test_an_app_whose_scripts_run_before_the_snapshot_is_still_seen_as_script_rendered(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    _spa(browser, snapshot_sees_content=True)

    connector = await make_connector()
    await connector.run_sync()

    assert connector.use_headless_browser is True
    assert db.pages()[INSIDE].record_name == "Inside"


async def test_a_server_rendered_site_needs_no_browser_and_its_start_page_is_fetched_once(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    browser.html(START_URL, "Home", "/next", text=LONG_TEXT)
    browser.html("http://site.test/next", "Next")

    connector = await make_connector()
    await connector.run_sync()

    assert connector.use_headless_browser is False
    assert browser.gets(START_URL) == 1, "the rendering check's fetch is the crawl's fetch"


async def test_the_verdict_is_reused_by_the_next_sync_instead_of_probing_again(
    browser: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
) -> None:
    _spa(browser, snapshot_sees_content=False)
    first = await make_connector()
    await first.run_sync()
    (saved,) = checkpoints.sync_points.values()
    assert saved["renderVerdict"] == "csr"
    probes = browser.browser_visits.count(START_URL)

    # Each sync builds its connector afresh, which used to probe the start page every time.
    second = await make_connector()

    assert second.use_headless_browser is True
    assert browser.browser_visits.count(START_URL) == probes


async def test_turning_robust_mode_on_takes_effect_at_the_next_sync(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    browser.html(START_URL, "Home", "/next", text=LONG_TEXT)
    browser.html("http://site.test/next", "Next")
    connector = await make_connector()
    await connector.run_sync()
    assert connector.use_headless_browser is False

    connector.config_service.sync["use_headless_browser"] = True
    await connector.run_sync()

    assert connector.use_headless_browser is True
    assert "http://site.test/next" in browser.browser_visits


async def test_turning_robust_mode_off_keeps_the_browser_for_a_script_rendered_site(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    _spa(browser, snapshot_sees_content=False)
    connector = await make_connector()  # detected as script-rendered
    connector.config_service.sync["use_headless_browser"] = True
    await connector.run_sync()

    connector.config_service.sync["use_headless_browser"] = False
    await connector.run_sync()

    assert connector.use_headless_browser is True


async def test_a_server_rendered_page_with_heavy_site_chrome_needs_no_browser(
    browser: FakeWeb, make_connector: MakeConnector,
) -> None:
    """RX-01: the served page is measured without its menus and footer, so the rendered one must be too."""
    nav = "".join(f"<a href='/section-{i}'>Section number {i} of the documentation</a> " for i in range(30))
    page = (
        f"<html><head><title>Home</title></head><body><header class='site-header'><nav>{nav}</nav></header>"
        f"<main><h1>Home</h1><p>{LONG_TEXT}</p></main><footer>{'Copyright and legal notices. ' * 20}</footer>"
        "</body></html>"
    ).encode()
    browser.add(START_URL, Page(body=page))

    connector = await make_connector()
    await connector.run_sync()

    assert connector.use_headless_browser is False


async def test_script_rendered_articles_under_a_server_rendered_home_page_are_rendered(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    """Found on a real support site: its home page is server-rendered, while each article's HTML is a
    shell (title, menus and a hidden "page can't be found" message) filled in by a script."""
    article = "http://site.test/article-v2/setting-up.html"
    shell = (b"<html><head><title>Setting up</title></head><body><main><h1>Setting up</h1>"
             b"<p>Was this article helpful? Sorry, that page can't be found.</p><div id='app'></div></main>"
             b"<script defer src='/app.js'></script></body></html>")
    browser.html(START_URL, "Home", "/article-v2/setting-up.html", text=LONG_TEXT)
    browser.add(article, Page(body=shell, rendered=html_page("Setting up", text=LONG_TEXT)))

    await (await make_connector()).run_sync()

    assert LONG_TEXT.strip() in browser.storage_docs[db.pages()[article].storage_document_id].decode()


async def test_a_server_rendered_page_that_scripts_add_to_needs_no_browser(
    browser: FakeWeb, make_connector: MakeConnector,
) -> None:
    """Found on a real marketing site: 3,256 characters served, 4,664 rendered (ratio 0.70) gave
    verdict=CSR, and the sync took 6x longer, with 8 of 29 pages timing out in the browser."""
    served = "Served by the server as plain HTML for every visitor. " * 40
    added = "Added by a script after the page has loaded. " * 21
    browser.add(START_URL, Page(body=html_page("Home", text=served), rendered=html_page("Home", text=served + added)))

    connector = await make_connector()
    await connector.run_sync()

    assert connector.use_headless_browser is False


async def test_once_a_section_needs_the_browser_its_other_pages_are_fetched_only_by_it(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    shell = b"<html><head><title>Help</title></head><body><div id='app'></div><script src='/app.js'></script></body></html>"
    articles = [f"http://site.test/help/article-{i}.html" for i in range(4)]
    browser.html(START_URL, "Home", *[a.removeprefix("http://site.test") for a in articles], text=LONG_TEXT)
    for i, article in enumerate(articles):
        browser.add(article, Page(body=shell, rendered=html_page(f"Article {i}", text=LONG_TEXT)))

    await (await make_connector()).run_sync()

    assert all(LONG_TEXT.strip() in browser.storage_docs[db.pages()[a].storage_document_id].decode() for a in articles)
    assert sum(browser.gets(a) for a in articles) == 1, "only the first article is also fetched plainly"


async def test_short_pages_with_scripts_that_really_are_short_are_checked_at_most_twice_per_section(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    tags = [f"http://site.test/tags/tag-{i}.html" for i in range(5)]
    browser.html(START_URL, "Home", *[t.removeprefix("http://site.test") for t in tags], text=LONG_TEXT)
    for i, tag in enumerate(tags):
        # Short, and with a script (analytics, say), but complete as served.
        browser.add(tag, Page(body=html_page(f"Tag {i}", body="<script src='/analytics.js'></script>")))

    connector = await make_connector()
    await connector.run_sync()

    assert len(db.pages()) == 6
    assert sum(1 for url in browser.browser_visits if url in tags) == 2
    assert connector.use_headless_browser is False


async def test_a_short_page_without_scripts_is_never_checked_in_the_browser(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    browser.html(START_URL, "Home", "/notes/short.html", text=LONG_TEXT)
    browser.html("http://site.test/notes/short.html", "Short")

    await (await make_connector()).run_sync()

    assert "http://site.test/notes/short.html" not in browser.browser_visits


async def test_a_page_the_rendering_check_read_is_not_served_to_a_later_sync(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector,
) -> None:
    browser.html(START_URL, "Home", "/next", text=LONG_TEXT)
    browser.html("http://site.test/next", "Next")
    connector = await make_connector()
    await connector.run_sync()
    # A check whose read the crawl never used (a single-page crawl, or a resumed one that had the page).
    assert await connector._served_text_length(START_URL)

    browser.html(START_URL, "Home v2", "/next", text=LONG_TEXT)
    await connector.run_sync()

    assert db.pages()[START_URL].record_name == "Home v2"
