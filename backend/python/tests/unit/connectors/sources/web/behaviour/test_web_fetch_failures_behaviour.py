"""How the crawler treats pages that fail: temporary failures are retried a bounded
number of times, permanent ones are not retried, and what is left over is shown
to the user with a plain reason and a next step.
"""

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    RecordingNotifications,
    VirtualClock,
)

from app.config.constants.arangodb import ProgressStatus

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
async def test_a_missing_page_is_asked_for_once_and_shown_as_not_found(
    status: int, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _home_linking_to_page(site)
    site.add(PAGE, Page(status=status, body=b"nope"))

    await (await make_connector()).run_sync()

    assert site.gets(PAGE) == 1
    assert PAGE not in site.browser_visits
    failed = db.pages()[PAGE]
    assert failed.indexing_status == ProgressStatus.FAILED.value
    assert failed.reason is not None and failed.reason.startswith(f"The page wasn't found ({status} ")


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
