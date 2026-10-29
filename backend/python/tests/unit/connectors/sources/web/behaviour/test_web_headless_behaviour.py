"""Crawling through the headless browser: Robust Mode, automatic detection of
script-rendered sites, and what happens when no browser can be started.

The browser is the fake from web_behaviour_fakes; ``Crawl4AIFetcher`` and the
connector's batching run for real.
"""

import asyncio
import json

import pytest
from fastapi import HTTPException
from web_behaviour_fakes import (
    HEAD_HANGS_UP,
    START_URL,
    FakeCheckpointStore,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    RecordingNotifications,
    VirtualClock,
    assert_crash_left_no_checkpoint,
    html_page,
)

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.sources.web import crawl4ai_fetcher
from app.connectors.sources.web.browser_supervisor import BrowserUnavailableError
from app.models.entities import FileRecord, RecordType

SHELL = b"<html><head><title>App</title></head><body><div id='root'></div></body></html>"
LONG_TEXT = "This paragraph is only there once the page's scripts have run. " * 5


def _legacy_record(url: str) -> FileRecord:
    """A page stored before content was kept, so opening it fetches the site live."""
    return FileRecord(
        id="legacy-1", org_id="org-1", record_name="Guide", record_type=RecordType.FILE,
        external_record_id=url, version=0, origin="CONNECTOR", connector_name="WEB",
        connector_id="web-1", weburl=url, is_file=True, mime_type=MimeTypes.HTML.value,
    )


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
    assert clock.slept(15.0)


async def test_a_script_rendered_site_is_detected_and_crawled_with_the_browser(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    rendered = html_page("App", "/inside", text=LONG_TEXT)
    browser.add(START_URL, Page(body=SHELL, rendered=rendered, pre_render_text_len=0))
    browser.add("http://site.test/inside", Page(body=SHELL, rendered=html_page("Inside", text=LONG_TEXT)))

    connector = await make_connector()
    await connector.run_sync()

    assert connector.use_headless_browser is True
    assert db.pages()["http://site.test/inside"].record_name == "Inside"


@pytest.mark.parametrize("robust", [False, True])
async def test_turning_a_connector_on_neither_loads_the_site_nor_starts_the_browser(
    browser: FakeWeb, make_connector: MakeConnector, robust: bool
) -> None:
    # Setup runs inside the request that turns the connector on; the site and the browser wait for the sync.
    browser.add(START_URL, Page(body=SHELL, rendered=html_page("App", text=LONG_TEXT), pre_render_text_len=0))

    connector = await make_connector(use_headless_browser=robust)

    assert browser.fetched_urls() == set()
    assert browser.browser_starts == 0 and browser.browser_visits == []
    assert connector.crawl4ai_fetcher is None


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


async def test_a_robots_txt_that_cant_be_read_puts_the_script_rendering_check_off_to_the_next_sync(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    # RFC 9309: an unreadable robots.txt allows nothing, so the browser waits; once a decision is
    # made it holds, so an undecided check is tried again when a sync can read robots.txt.
    browser.add("http://site.test/robots.txt", [
        *[Page(status=503, body=b"")] * 3,  # every attempt the first sync makes
        Page(body=b"User-agent: *\nAllow: /\n", content_type="text/plain"),
    ])
    browser.add(START_URL, Page(body=SHELL, rendered=html_page("App", "/inside", text=LONG_TEXT),
                                pre_render_text_len=0))
    browser.add("http://site.test/inside", Page(body=SHELL, rendered=html_page("Inside", text=LONG_TEXT)))

    connector = await make_connector()
    await connector.run_sync()
    assert browser.browser_visits == []
    assert connector._script_check_pending is True

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


async def test_a_start_page_redirect_onto_a_site_whose_robots_txt_cant_be_read_keeps_the_check_pending(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    landing = "http://www.site.test/"
    browser.add("http://site.test/robots.txt", Page(body=b"User-agent: *\nAllow: /\n", content_type="text/plain"))
    browser.add("http://www.site.test/robots.txt", [
        *[Page(status=503, body=b"")] * 3,  # every attempt of the first sync
        Page(body=b"User-agent: *\nAllow: /\n", content_type="text/plain"),
    ])
    browser.add(START_URL, Page(status=301, location=landing, content_type=None))
    browser.add(landing, Page(body=SHELL, rendered=html_page("App", text=LONG_TEXT), pre_render_text_len=0))

    connector = await make_connector(follow_external=True)
    await connector.run_sync()
    assert connector._script_check_pending is True
    assert browser.browser_visits == []

    await connector.run_sync()

    assert connector._script_check_pending is False
    assert connector.use_headless_browser is True


async def test_without_a_working_browser_a_plain_site_still_syncs(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.browser_broken = True
    browser.html(START_URL, "Home", "/next")
    browser.html("http://site.test/next", "Next")

    connector = await make_connector()
    await connector.run_sync()

    assert set(db.pages()) == {START_URL, "http://site.test/next"}


async def test_robust_mode_without_a_working_browser_fails_the_sync(
    browser: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    browser.browser_broken = True
    browser.html(START_URL, "Home")

    connector = await make_connector(use_headless_browser=True)
    with pytest.raises(BrowserUnavailableError):
        await connector.run_sync()

    (saved,) = checkpoints.sync_points.values()
    run = json.loads(saved["lastRun"])
    assert (run["outcome"], run["error"]) == ("failed", "browser_unavailable")
    assert db.pages() == {}


async def test_a_sync_holds_the_browser_only_while_it_runs(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home")
    first = await make_connector(use_headless_browser=True)
    second = await make_connector(use_headless_browser=True)

    await first.run_sync()
    assert first.crawl4ai_fetcher is None
    assert crawl4ai_fetcher._ref_count == 0
    await second.run_sync()

    assert browser.browser_starts == 1  # the second run came within the idle grace
    assert crawl4ai_fetcher._idle_close is not None


async def test_concurrent_fallbacks_in_a_run_take_one_reference(
    browser: FakeWeb, make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector = await make_connector()

    first, second = await asyncio.gather(connector._ensure_crawl4ai_fetcher(), connector._ensure_crawl4ai_fetcher())

    assert first is second is not None
    assert crawl4ai_fetcher._ref_count == 1
    monkeypatch.setenv("WEB_BROWSER_IDLE_CLOSE_SECONDS", "0")
    await connector._release_crawl4ai_fetcher()
    assert crawl4ai_fetcher._shared_instance is None


async def test_a_failed_sync_gives_the_browser_back(
    browser: FakeWeb, make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser.html(START_URL, "Home")
    connector = await make_connector(use_headless_browser=True)

    async def fails(*_: object, **__: object) -> None:
        assert crawl4ai_fetcher._ref_count == 1
        raise RuntimeError("store down")

    monkeypatch.setattr(connector, "process_retry_urls", fails)

    with pytest.raises(RuntimeError, match="store down"):
        await connector.run_sync()

    assert connector.crawl4ai_fetcher is None
    assert crawl4ai_fetcher._ref_count == 0


async def test_a_sync_that_only_probed_for_scripts_holds_no_reference(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", text=LONG_TEXT)
    connector = await make_connector()

    await connector.run_sync()

    assert browser.browser_visits == [START_URL]  # the rendering check, then plain HTTP
    assert connector.crawl4ai_fetcher is None
    assert crawl4ai_fetcher._ref_count == 0


async def test_cleanup_after_a_sync_gives_nothing_back_twice(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home")
    other_holder = await crawl4ai_fetcher.get_shared_fetcher()
    connector = await make_connector(use_headless_browser=True)

    await connector.run_sync()
    await connector.cleanup()

    assert crawl4ai_fetcher._ref_count == 1
    assert crawl4ai_fetcher._shared_instance is other_holder
    await crawl4ai_fetcher.release_shared_fetcher()


async def test_opening_a_page_live_leaves_a_running_syncs_browser_alone(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home")
    browser.html("http://site.test/guide", "Guide", text="Live through the browser")
    connector = await make_connector(use_headless_browser=True)
    syncs_fetcher = await connector._ensure_crawl4ai_fetcher()

    response = await connector.stream_record(_legacy_record("http://site.test/guide"))

    assert b"Live through the browser" in b"".join([chunk async for chunk in response.body_iterator])
    assert connector.crawl4ai_fetcher is syncs_fetcher
    assert crawl4ai_fetcher._ref_count == 1


async def test_opening_a_page_gives_the_browser_back_when_it_fails(
    browser: FakeWeb, make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    browser.html(START_URL, "Home")
    connector = await make_connector(use_headless_browser=True)

    async def dead(*_: object, **__: object) -> object:
        raise BrowserUnavailableError("gone")

    monkeypatch.setattr(crawl4ai_fetcher.Crawl4AIFetcher, "fetch", dead)

    with pytest.raises(HTTPException):
        await connector.stream_record(_legacy_record("http://site.test/guide"))

    assert connector.crawl4ai_fetcher is None
    assert crawl4ai_fetcher._ref_count == 0


async def test_pages_opened_before_any_sync_keep_their_cookies_to_their_own_connector(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home")
    browser.html("http://site.test/guide", "Guide", text="Live through the browser")
    first = await make_connector(use_headless_browser=True)
    second = await make_connector(use_headless_browser=True, connector_id="web-2")

    for connector in (first, second, first):
        await connector.stream_record(_legacy_record("http://site.test/guide"))

    first_key, second_key, first_again = browser.browser_context_keys
    assert first_key is not None
    assert first_again == first_key
    assert second_key not in (None, first_key)


async def test_a_sync_starts_a_browser_context_of_its_own_that_later_pages_opened_join(
    browser: FakeWeb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/guide")
    browser.html("http://site.test/guide", "Guide", text="Live through the browser")
    connector = await make_connector(use_headless_browser=True)
    await connector.stream_record(_legacy_record("http://site.test/guide"))
    [live_key] = browser.browser_context_keys

    await connector.run_sync()
    sync_keys = set(browser.browser_context_keys[1:])
    await connector.stream_record(_legacy_record("http://site.test/guide"))

    assert len(sync_keys) == 1
    assert None not in sync_keys
    assert live_key is not None
    assert live_key not in sync_keys
    assert browser.browser_context_keys[-1] in sync_keys


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


# The first wait of the crawl's per-site retry, which a blocked page in Robust Mode goes through.
BROWSER_RETRY_LAST_WAIT = 15.0


@pytest.mark.parametrize(
    ("link", "pages"),
    [
        pytest.param("/handbook", {
            "http://site.test/handbook": Page(status=302, location="/handbook.pdf", content_type=None),
            "http://site.test/handbook.pdf": Page(status=403, rendered_status=200, body=b"no", content_type="application/pdf"),
        }, id="redirect-onto-blocked-pdf"),
        pytest.param("/handbook", {
            "http://site.test/handbook": Page(status=302, location="/handbook.pdf", content_type=None),
            "http://site.test/handbook.pdf": Page(status=403, body=b"no", content_type="application/pdf"),
        }, id="redirect-onto-pdf-the-browser-is-refused"),
        pytest.param("/manual.pdf", {
            "http://site.test/manual.pdf": Page(status=403, body=b"no", content_type="application/pdf"),
        }, id="blocked-pdf"),
        pytest.param("/blocked", {
            "http://site.test/blocked": Page(status=403, body=b"<html><body>Access denied</body></html>"),
        }, id="blocked-html-page"),
    ],
)
async def test_robust_mode_retries_blocked_pages_and_documents_through_the_per_site_retry(
    link: str, pages: dict,
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector,
) -> None:
    browser.html(START_URL, "Home", link)
    for url, page in pages.items():
        browser.add(url, page)

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert clock.slept(BROWSER_RETRY_LAST_WAIT)
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

    assert not clock.slept(BROWSER_RETRY_LAST_WAIT)
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

    assert not clock.slept(BROWSER_RETRY_LAST_WAIT)
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

    assert set(db.pages()) == {START_URL, "http://site.test/handbook"}
    assert "403 Forbidden" in (db.pages()["http://site.test/handbook"].reason or "")


async def test_robust_mode_still_retries_an_html_page_the_browser_got_no_answer_from(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/flaky")
    browser.add("http://site.test/flaky", Page(body=b"<html><body>Flaky</body></html>", browser_aborts=True))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert clock.slept(BROWSER_RETRY_LAST_WAIT)
    assert db.pages()["http://site.test/flaky"].indexing_status == ProgressStatus.FAILED.value


async def test_robust_mode_skips_an_oversized_file_behind_an_aborted_redirect_without_retrying(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    pdf = "http://site.test/handbook.pdf"
    browser.html(START_URL, "Home", "/handbook")
    browser.redirect("http://site.test/handbook", "/handbook.pdf")
    browser.add(pdf, Page(body=b"x" * (2 * 1024 * 1024), content_type="application/pdf", browser_aborts=True))

    await (await make_connector(use_headless_browser=True, max_size_mb=1)).run_sync()

    assert not clock.slept(BROWSER_RETRY_LAST_WAIT)
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
    assert not clock.slept(BROWSER_RETRY_LAST_WAIT)
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
    assert not clock.slept(BROWSER_RETRY_LAST_WAIT)
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
    assert not clock.slept(BROWSER_RETRY_LAST_WAIT)


async def test_robust_mode_takes_the_probe_s_error_for_a_page_instead_of_retrying_the_browser(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    gone = "http://site.test/gone"
    browser.html(START_URL, "Home", "/gone")
    browser.add(gone, Page(status=404, body=b"<html><body>Not here</body></html>", browser_aborts=True))

    connector = await make_connector(use_headless_browser=True)
    await connector.run_sync()

    assert browser.gets(gone) == 0
    assert not clock.slept(BROWSER_RETRY_LAST_WAIT)
    # The probe's real status, not the browser's silence, is what the page is filed under.
    assert connector.retry_urls[gone].status_code == 404
    assert gone not in db.pages()


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
    assert clock.slept(BROWSER_RETRY_LAST_WAIT)


async def test_robust_mode_reports_no_answer_as_unreachable(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/page")
    browser.add("http://site.test/page", Page(hang_up=True))

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert db.pages()["http://site.test/page"].reason == (
        "We couldn't reach this page. Check the URL is correct and publicly reachable, then sync again."
    )


def _home_linking_to(browser: FakeWeb, count: int) -> None:
    links = [f"/p{i}" for i in range(count)]
    browser.html(START_URL, "Home", *links)
    for link in links:
        browser.html(f"http://site.test{link}", link)


async def test_robust_mode_restarts_a_browser_that_dies_mid_crawl_and_loses_no_page(
    browser: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    _home_linking_to(browser, 12)
    browser.browser_dies_after = 4

    await (await make_connector(use_headless_browser=True)).run_sync()

    assert len(db.pages()) == 13
    assert all(r.indexing_status != ProgressStatus.FAILED.value for r in db.pages().values())
    assert browser.browser_starts == 2
    assert clock.sleeps == []


async def test_robust_mode_fails_the_sync_when_the_browser_cannot_be_restarted(
    browser: FakeWeb,
    db: FakeRecordsDb,
    clock: VirtualClock,
    checkpoints: FakeCheckpointStore,
    notifications: RecordingNotifications,
    make_connector: MakeConnector,
) -> None:
    _home_linking_to(browser, 12)
    browser.browser_dies_after = 4
    browser.browser_stays_dead = True
    connector = await make_connector(use_headless_browser=True)

    with pytest.raises(BrowserUnavailableError):
        await connector.run_sync()

    assert clock.sleeps == []
    assert all(r.indexing_status != ProgressStatus.FAILED.value for r in db.pages().values())
    assert_crash_left_no_checkpoint(checkpoints)
    assert (await notifications.delivered())[-1]["title"] == "Web crawl stopped"
