"""How the crawler treats pages that fail: temporary failures are retried a bounded
number of times, permanent ones are not retried, and what is left over is shown
to the user with a plain reason and a next step.
"""

import asyncio
import json

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeCheckpointStore,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    RecordingNotifications,
    VirtualClock,
    html_page,
)

from app.config.constants.arangodb import ProgressStatus
from app.connectors.sources.web.politeness import shared_fetch_slots, shared_host_gate

PAGE = "http://site.test/page"


def _home_linking_to_page(site: FakeWeb) -> None:
    site.html(START_URL, "Home", "/page", "/fine")
    site.html("http://site.test/fine", "Fine")


async def test_a_temporary_outage_is_retried_and_the_page_indexed(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, [Page(status=503, body=b""), Page(status=502, body=b""), Page(body=b"<title>Page</title>ok")])

    await (await make_connector()).run_sync()

    page = db.pages()[PAGE]
    assert page.indexing_status != ProgressStatus.FAILED.value
    assert page.record_name == "Page"


async def test_a_page_that_stays_down_is_retried_a_bounded_number_of_times(
    site: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=503, body=b""))

    await (await make_connector()).run_sync()

    assert 1 < site.gets(PAGE) <= 60
    assert sum(clock.sleeps) < 3 * 60 * 60
    failed = db.pages()[PAGE]
    assert failed.indexing_status == ProgressStatus.FAILED.value
    assert failed.reason == (
        "The site didn't respond properly (503 Service Unavailable). PipesHub will try again on the next sync."
    )
    assert "http://site.test/fine" in db.pages()


@pytest.mark.parametrize("status", [404, 410])
async def test_a_missing_page_is_asked_for_once_and_reported_not_stored(
    status: int, site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=status, body=b"nope"))

    await (await make_connector()).run_sync()

    assert site.gets(PAGE) == 1
    assert PAGE not in site.browser_visits
    assert PAGE not in db.pages()
    (saved,) = checkpoints.sync_points.values()
    assert json.loads(saved["lastRun"])["failed_by_status"] == {str(status): 1}


async def test_a_page_behind_a_login_is_shown_as_refused(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=401, body=b"login"))

    await (await make_connector()).run_sync()

    assert site.gets(PAGE) == 1
    assert db.pages()[PAGE].reason == (
        "The page refused access (401 Unauthorized). It may need a login or block automated visitors; "
        "make sure it's publicly reachable, then sync again."
    )


async def test_a_long_retry_after_leaves_the_page_for_the_next_sync_without_waiting(
    site: FakeWeb, db: FakeRecordsDb, clock: VirtualClock, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=429, body=b"", headers={"Retry-After": "3600"}))

    await (await make_connector()).run_sync()

    assert max(clock.sleeps, default=0) < 3600
    assert site.gets(PAGE) <= 4
    failed = db.pages()[PAGE]
    assert failed.reason == (
        "The site didn't respond properly (429 Too Many Requests). PipesHub will try again on the next sync."
    )
    assert "http://site.test/fine" in db.pages()


async def test_a_bot_block_tells_the_user_the_page_must_be_publicly_reachable(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=403, body=b"blocked"))

    await (await make_connector()).run_sync()

    assert db.pages()[PAGE].reason == (
        "The page refused access (403 Forbidden). It may need a login or block automated visitors; "
        "make sure it's publicly reachable, then sync again."
    )


async def test_a_browser_that_renders_nothing_does_not_hide_the_real_failure(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=403, body=b"blocked", rendered=b"", rendered_status=200))

    await (await make_connector()).run_sync()

    failed = db.pages()[PAGE]
    assert failed.indexing_status == ProgressStatus.FAILED.value
    assert "403 Forbidden" in (failed.reason or "")


async def test_a_server_that_hangs_up_leaves_a_failed_page_with_a_next_step(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(hang_up=True))

    await (await make_connector()).run_sync()

    failed = db.pages()[PAGE]
    assert failed.indexing_status == ProgressStatus.FAILED.value
    assert failed.reason == "We couldn't reach this page. Check the URL is correct and publicly reachable, then sync again."
    assert "http://site.test/fine" in db.pages()


async def test_the_sync_summary_counts_the_pages_that_failed(
    site: FakeWeb, db: FakeRecordsDb, notifications: RecordingNotifications, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=503, body=b""))

    await (await make_connector()).run_sync()

    summary = (await notifications.delivered())[-1]
    assert summary["title"] == "Web crawl completed"
    assert summary["message"].startswith("Failed to crawl 1 pages.")


async def test_the_connection_check_reports_an_unreachable_site(
    site: FakeWeb, notifications: RecordingNotifications, make_connector: MakeConnector
) -> None:
    site.add(START_URL, Page(status=404, body=b"missing"))
    connector = await make_connector()

    assert await connector.test_connection_and_access() is False
    assert (await notifications.delivered())[-1]["message"] == f"Website {START_URL} returned status 404"


async def test_the_connection_check_passes_for_a_reachable_site(
    site: FakeWeb, notifications: RecordingNotifications, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home")
    connector = await make_connector()

    assert await connector.test_connection_and_access() is True
    assert await notifications.delivered() == []


async def test_a_cdn_error_code_is_reported_as_the_site_s_answer_not_as_unreachable(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=522, body=b"origin timed out"))

    await (await make_connector()).run_sync()

    reason = db.pages()[PAGE].reason or ""
    assert "(522" in reason and "couldn't reach" not in reason


async def test_waiting_on_a_slowed_down_site_holds_none_of_the_shared_fetch_slots(
    browser: FakeWeb, make_connector: MakeConnector,
) -> None:
    """RS-06: one throttled site must not starve the other connectors of the process's fetch slots."""
    _home_linking_to_page(browser)
    connector = await make_connector()
    site = shared_host_gate._site("site.test")
    site.active = site.limit
    waiting = asyncio.ensure_future(connector._fetch_page(PAGE, 1, None))
    await asyncio.sleep(0.2)
    assert not waiting.done() and shared_fetch_slots.active == 0
    site.active = 0
    await asyncio.wait_for(waiting, 5)
    assert shared_fetch_slots.active == 0


async def test_a_site_that_rate_limits_every_page_is_left_for_the_next_sync(
    site: FakeWeb, make_connector: MakeConnector, clock: VirtualClock,
) -> None:
    """Found on a real docs site that answered 429 (Retry-After: 0) to every request from the crawler's address.

    40 such pages took 604 GETs and an hour of back-off; a site of 1,000 would take a day of
    requests the site has already refused. Once the site refuses page after page with no
    success, the crawl should stop asking it and leave the rest for the next sync.
    """
    links = [f"http://site.test/p{i}.html" for i in range(40)]
    site.html(START_URL, "Home", *links)
    for url in links:
        site.add(url, Page(status=429))

    await (await make_connector()).run_sync()

    assert sum(1 for method, _ in site.requests if method == "GET") <= 3 * len(links)
    assert clock.offset < 15 * 60


async def test_a_site_that_rate_limits_only_some_pages_is_crawled_to_the_end(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector, checkpoints: FakeCheckpointStore,
) -> None:
    links = [f"http://site.test/p{i}.html" for i in range(30)]
    site.html(START_URL, "Home", *links)
    for i, url in enumerate(links):
        site.add(url, Page(status=429) if i % 3 == 0 else Page(body=html_page(f"p{i}")))

    await (await make_connector()).run_sync()

    (saved,) = checkpoints.sync_points.values()
    assert json.loads(saved["lastRun"])["stop_reason"] is None
    assert len([u for u in db.pages() if u in links]) >= 20
