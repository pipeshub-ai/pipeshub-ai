"""Crawling through the headless browser: Robust Mode, automatic detection of
script-rendered sites, and what happens when no browser can be started.

The browser is the fake from web_behaviour_fakes; ``Crawl4AIFetcher`` and the
connector's batching run for real.
"""

import pytest
from web_behaviour_fakes import (
    HEAD_HANGS_UP,
    START_URL,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    VirtualClock,
    html_page,
)

from app.config.constants.arangodb import ProgressStatus
from app.connectors.sources.web import crawl4ai_fetcher

SHELL = b"<html><head><title>App</title></head><body><div id='root'></div></body></html>"
LONG_TEXT = "This paragraph is only there once the page's scripts have run. " * 5


async def test_robust_mode_crawls_every_page_through_the_browser(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/a", "/b")
    browser.html("http://site.test/a", "A", "/b")
    browser.html("http://site.test/b", "B")

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/a", "http://site.test/b"}
    assert sorted(browser.browser_visits) == [START_URL, "http://site.test/a", "http://site.test/b"]
    assert browser.fetched_urls() == set()


async def test_robust_mode_respects_the_page_cap(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    links = [f"/p{i}" for i in range(12)]
    browser.html(START_URL, "Home", *links)
    for link in links:
        browser.html(f"http://site.test{link}", link)

    await (await make_connector(use_headless_browser=True, max_pages=5)).run_sync()

    assert len(browser.browser_visits) == 5
    assert len(db.pages()) == 5


async def test_robust_mode_backs_off_and_retries_a_rate_limited_page(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/busy")
    browser.add("http://site.test/busy", [Page(status=429), Page(body=html_page("Busy"))])

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert db.pages()["http://site.test/busy"].record_name == "Busy"
    assert 15.0 in clock.sleeps


async def test_a_script_rendered_site_is_detected_and_crawled_with_the_browser(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    rendered = html_page("App", "/inside", text=LONG_TEXT)
    browser.add(START_URL, Page(body=SHELL, rendered=rendered, pre_render_text_len=0))
    browser.add("http://site.test/inside", Page(body=SHELL, rendered=html_page("Inside", text=LONG_TEXT)))

    connector = await make_connector()
    assert connector.use_headless_browser is True
    await connector.run_sync()

    assert db.pages()["http://site.test/inside"].record_name == "Inside"


async def test_a_server_rendered_site_is_crawled_without_the_browser(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/next", text=LONG_TEXT)
    browser.html("http://site.test/next", "Next")

    connector = await make_connector()
    await connector.run_sync()

    assert connector.use_headless_browser is False
    assert browser.browser_visits == [START_URL]
    assert set(db.pages()) == {START_URL, "http://site.test/next"}


async def test_the_script_rendering_check_does_not_load_a_start_page_robots_txt_disallows(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.add("http://site.test/robots.txt",
                Page(body=b"User-agent: *\nDisallow: /\n", content_type="text/plain"))
    browser.add(START_URL, Page(body=SHELL, rendered=html_page("App", text=LONG_TEXT), pre_render_text_len=0))

    connector = await make_connector()
    await connector.run_sync()

    assert browser.browser_visits == []
    assert db.pages() == {}


async def test_the_script_rendering_check_does_not_follow_the_start_page_to_a_disallowed_address(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.add("http://site.test/robots.txt",
                Page(body=b"User-agent: *\nDisallow: /private/\n", content_type="text/plain"))
    browser.redirect(START_URL, "/private/")
    browser.add("http://site.test/private/",
                Page(body=SHELL, rendered=html_page("App", text=LONG_TEXT), pre_render_text_len=0))

    await make_connector()

    assert browser.browser_visits == []


async def test_a_robots_txt_that_cant_be_read_at_setup_puts_the_script_rendering_check_off_to_the_sync(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    # RFC 9309: an unreadable robots.txt allows nothing, so the browser waits; the check runs only
    # at setup otherwise, so it is tried again when the sync can read robots.txt.
    browser.add("http://site.test/robots.txt", [
        Page(status=503, body=b""),
        Page(body=b"User-agent: *\nAllow: /\n", content_type="text/plain"),
    ])
    browser.add(START_URL, Page(body=SHELL, rendered=html_page("App", "/inside", text=LONG_TEXT),
                                pre_render_text_len=0))
    browser.add("http://site.test/inside", Page(body=SHELL, rendered=html_page("Inside", text=LONG_TEXT)))

    connector = await make_connector()
    assert browser.browser_visits == []
    await connector.run_sync()

    assert connector.use_headless_browser is True
    assert db.pages()["http://site.test/inside"].record_name == "Inside"


async def test_while_robots_txt_cant_be_read_the_browser_never_opens_the_start_page(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.add("http://site.test/robots.txt", Page(status=503, body=b""))
    browser.add(START_URL, Page(body=SHELL, rendered=html_page("App", text=LONG_TEXT), pre_render_text_len=0))

    connector = await make_connector()
    await connector.run_sync()
    await connector.run_sync()

    assert browser.browser_visits == []


async def test_without_a_working_browser_a_plain_site_still_syncs(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.browser_broken = True
    browser.html(START_URL, "Home", "/next")
    browser.html("http://site.test/next", "Next")

    connector = await make_connector()
    await connector.run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/next"}


async def test_robust_mode_without_a_working_browser_fails_to_start(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.browser_broken = True
    browser.html(START_URL, "Home")

    await make_connector(use_headless_browser=True, expect_init=False)


async def test_the_shared_browser_is_closed_when_the_last_connector_is_cleaned_up(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home")
    first = await make_connector(use_headless_browser=True)
    second = await make_connector(use_headless_browser=True)
    assert first.crawl4ai_fetcher is second.crawl4ai_fetcher

    await first.cleanup()
    assert crawl4ai_fetcher._shared_instance is second.crawl4ai_fetcher

    await second.cleanup()
    assert crawl4ai_fetcher._shared_instance is None


async def test_robust_mode_stores_a_redirected_page_once(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/old-name", "/new-name")
    browser.redirect("http://site.test/old-name", "/new-name")
    browser.html("http://site.test/new-name", "New name")

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/new-name"}
    assert len(browser.storage_uploads) == 2


async def test_robust_mode_does_not_store_a_redirect_that_leaves_the_site(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/go", "/stay")
    browser.redirect("http://site.test/go", "http://other.test/landing")
    browser.html("http://other.test/landing", "Landing")
    browser.html("http://site.test/stay", "Stay")

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/stay"}


BROWSER_RETRY_LAST_WAIT = 240.0  # the fifth wait of Robust Mode's 15s-doubling retry of blocked pages


@pytest.mark.parametrize(
    ("link", "pages", "browser_retried"),
    [
        pytest.param("/handbook", {
            "http://site.test/handbook": Page(status=302, location="/handbook.pdf", content_type=None),
            "http://site.test/handbook.pdf": Page(status=403, rendered_status=200, body=b"no", content_type="application/pdf"),
        }, False, id="redirect-onto-blocked-pdf"),
        pytest.param("/handbook", {
            "http://site.test/handbook": Page(status=302, location="/handbook.pdf", content_type=None),
            "http://site.test/handbook.pdf": Page(status=403, body=b"no", content_type="application/pdf"),
        }, False, id="redirect-onto-pdf-the-browser-is-refused"),
        pytest.param("/manual.pdf", {
            "http://site.test/manual.pdf": Page(status=403, body=b"no", content_type="application/pdf"),
        }, False, id="blocked-pdf"),
        pytest.param("/blocked", {
            "http://site.test/blocked": Page(status=403, body=b"<html><body>Access denied</body></html>"),
        }, True, id="blocked-html-page"),
    ],
)
async def test_robust_mode_retries_blocked_pages_but_not_blocked_documents(
    link: str, pages: dict, browser_retried: bool,
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector,
) -> None:
    browser.html(START_URL, "Home", link)
    for url, page in pages.items():
        browser.add(url, page)

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert (BROWSER_RETRY_LAST_WAIT in clock.sleeps) is browser_retried
    assert db.pages()[f"http://site.test{link}"].indexing_status == ProgressStatus.FAILED.value
    assert set(db.pages()) == {START_URL, f"http://site.test{link}"}


async def test_robust_mode_fetches_a_redirected_file_the_browser_could_not_open(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    pdf = "http://site.test/handbook.pdf"
    browser.html(START_URL, "Home", "/handbook")
    browser.redirect("http://site.test/handbook", "/handbook.pdf")
    browser.add(pdf, Page(body=b"%PDF-1.4 handbook", content_type="application/pdf", rendered_status=403))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert BROWSER_RETRY_LAST_WAIT not in clock.sleeps
    assert browser.storage_docs[db.pages()[pdf].storage_document_id] == b"%PDF-1.4 handbook"
    assert set(db.pages()) == {START_URL, pdf}


@pytest.mark.parametrize("single_page", [False, True], ids=["crawl", "single-page"])
async def test_robust_mode_follows_a_redirect_the_browser_aborted_onto_a_file(
    single_page: bool, browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    pdf = "http://site.test/handbook.pdf"
    browser.html(START_URL, "Home", "/handbook")
    browser.redirect("http://site.test/handbook", "/handbook.pdf")
    browser.add(pdf, Page(body=b"%PDF-1.4 handbook", content_type="application/pdf", browser_aborts=True))
    start = "http://site.test/handbook" if single_page else START_URL

    await (await make_connector(start, crawl_type="single" if single_page else "recursive",
                                use_headless_browser=True)).run_sync()

    assert BROWSER_RETRY_LAST_WAIT not in clock.sleeps
    assert browser.storage_docs[db.pages()[pdf].storage_document_id] == b"%PDF-1.4 handbook"
    assert "http://site.test/handbook" not in db.pages()


async def test_robust_mode_keeps_the_real_error_for_an_aborted_redirect_onto_a_blocked_file(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/handbook")
    browser.redirect("http://site.test/handbook", "/handbook.pdf")
    browser.add("http://site.test/handbook.pdf",
                Page(status=403, body=b"no", content_type="application/pdf", browser_aborts=True))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert BROWSER_RETRY_LAST_WAIT not in clock.sleeps
    assert set(db.pages()) == {START_URL, "http://site.test/handbook"}
    assert "403 Forbidden" in (db.pages()["http://site.test/handbook"].reason or "")


async def test_robust_mode_still_retries_an_html_page_the_browser_got_no_answer_from(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/flaky")
    browser.add("http://site.test/flaky", Page(body=b"<html><body>Flaky</body></html>", browser_aborts=True))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert BROWSER_RETRY_LAST_WAIT in clock.sleeps
    assert db.pages()["http://site.test/flaky"].indexing_status == ProgressStatus.FAILED.value


async def test_robust_mode_skips_an_oversized_file_behind_an_aborted_redirect_without_retrying(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    pdf = "http://site.test/handbook.pdf"
    browser.html(START_URL, "Home", "/handbook")
    browser.redirect("http://site.test/handbook", "/handbook.pdf")
    browser.add(pdf, Page(body=b"x" * (2 * 1024 * 1024), content_type="application/pdf", browser_aborts=True))

    await (await make_connector(use_headless_browser=True, max_size_mb=1)).run_sync()

    assert BROWSER_RETRY_LAST_WAIT not in clock.sleeps
    assert browser.gets(pdf) == 0
    # Listed where the file is, as too large, and nothing downloaded.
    too_large = db.pages()[pdf]
    assert too_large.storage_document_id is None
    assert (too_large.reason or "").startswith("This file is larger than this connector's 1 MB size limit")
    assert "http://site.test/handbook" not in db.pages()


@pytest.mark.parametrize(
    "browser_behaviour",
    [{}, {"browser_aborts": True}, {"rendered_status": 403}],
    ids=["browser-lands", "browser-aborts", "browser-refused-after-landing"],
)
@pytest.mark.parametrize(
    ("target", "settings"),
    [
        pytest.param("http://other.test/report.pdf", {}, id="another-site"),
        pytest.param("http://site.test/blog/report.pdf", {"url_should_contain": ["/docs/"]}, id="url-should-contain"),
    ],
)
async def test_robust_mode_never_downloads_a_redirected_file_outside_the_crawl(
    target: str, settings: dict, browser_behaviour: dict,
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector,
) -> None:
    browser.html(START_URL, "Home", "/docs/report")
    browser.redirect("http://site.test/docs/report", target)
    browser.add(target, Page(body=b"%PDF-1.4 elsewhere", content_type="application/pdf", **browser_behaviour))

    await (await make_connector(use_headless_browser=True, **settings)).run_sync()

    assert [method for method, url in browser.requests if url == target] == []
    assert BROWSER_RETRY_LAST_WAIT not in clock.sleeps
    assert target not in db.pages()


async def test_robust_mode_probes_with_get_when_head_is_refused_and_stays_off_other_sites(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    target = "http://other.test/report.pdf"
    browser.html(START_URL, "Home", "/docs/report")
    browser.add("http://site.test/docs/report", Page(status=302, location=target, content_type=None, head_status=405))
    browser.add(target, Page(body=b"%PDF-1.4 elsewhere", content_type="application/pdf", browser_aborts=True))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert [method for method, url in browser.requests if url == target] == []
    assert BROWSER_RETRY_LAST_WAIT not in clock.sleeps
    assert target not in db.pages()


async def test_robust_mode_probes_with_get_when_head_is_refused_and_fetches_an_in_scope_file(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    pdf = "http://site.test/docs/report.pdf"
    browser.html(START_URL, "Home", "/docs/report")
    browser.add("http://site.test/docs/report", Page(status=302, location=pdf, content_type=None, head_status=405))
    browser.add(pdf, Page(body=b"%PDF-1.4 report", content_type="application/pdf", browser_aborts=True))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert browser.storage_docs[db.pages()[pdf].storage_document_id] == b"%PDF-1.4 report"
    assert BROWSER_RETRY_LAST_WAIT not in clock.sleeps


async def test_robust_mode_takes_the_probe_s_error_for_a_page_instead_of_retrying_the_browser(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    gone = "http://site.test/gone"
    browser.html(START_URL, "Home", "/gone")
    browser.add(gone, Page(status=404, body=b"<html><body>Not here</body></html>", browser_aborts=True))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert browser.gets(gone) == 0
    assert BROWSER_RETRY_LAST_WAIT not in clock.sleeps
    # The probe's real status, not the browser's silence, is what the failed page reports.
    assert (db.pages()[gone].reason or "").startswith("The page wasn't found (404 Not Found)")


@pytest.mark.parametrize(
    ("link", "target", "settings"),
    [
        pytest.param("/docs/report.pdf", "http://other.test/report.pdf", {}, id="redirects-off-site"),
        pytest.param("/blog/report.pdf", "http://site.test/blog/report.pdf", {"url_should_contain": ["/docs/"]},
                     id="fails-url-should-contain"),
    ],
)
async def test_robust_mode_never_requests_a_linked_file_outside_the_crawl(
    link: str, target: str, settings: dict, browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", link)
    if f"http://site.test{link}" != target:
        browser.redirect(f"http://site.test{link}", target)
    browser.add(target, Page(body=b"%PDF-1.4 elsewhere", content_type="application/pdf"))

    await (await make_connector(use_headless_browser=True, **settings)).run_sync()

    assert [method for method, url in browser.requests if url == target] == []
    assert target not in db.pages()


@pytest.mark.parametrize("aborts", [False, True], ids=["linked-file", "aborted-redirect"])
async def test_robust_mode_probes_with_get_when_head_fails_and_still_fetches_the_file(
    aborts: bool, browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    pdf = "http://site.test/docs/report.pdf"
    link = "/docs/report.pdf" if not aborts else "/docs/report"
    browser.html(START_URL, "Home", link)
    if aborts:
        browser.add("http://site.test/docs/report",
                    Page(status=302, location=pdf, content_type=None, head_status=HEAD_HANGS_UP))
        browser.add(pdf, Page(body=b"%PDF-1.4 report", content_type="application/pdf", browser_aborts=True))
    else:
        browser.add(pdf, Page(body=b"%PDF-1.4 report", content_type="application/pdf", head_status=HEAD_HANGS_UP))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert browser.storage_docs[db.pages()[pdf].storage_document_id] == b"%PDF-1.4 report"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (403, "The page refused access (403 Forbidden)."),
        (503, "The site didn't respond properly (503 Service Unavailable)."),
    ],
)
async def test_robust_mode_reports_the_status_the_site_really_sent(
    status: int, reason: str, browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/page")
    browser.add("http://site.test/page", Page(status=status, body=b"<html><body>no</body></html>"))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert (db.pages()["http://site.test/page"].reason or "").startswith(reason)
    assert BROWSER_RETRY_LAST_WAIT in clock.sleeps


async def test_robust_mode_reports_no_answer_as_unreachable(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/page")
    browser.add("http://site.test/page", Page(hang_up=True))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert db.pages()["http://site.test/page"].reason == (
        "We couldn't reach this page. Check the URL is correct and publicly reachable, then sync again."
    )
