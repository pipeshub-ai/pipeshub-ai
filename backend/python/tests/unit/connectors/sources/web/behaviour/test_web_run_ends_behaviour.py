"""Every sync ends, whatever a retried page does on its second try.

A page that keeps asking the crawler to back off (429/503) outlasts the fetch layer's own
backoff and is queued for a retry at the end of the crawl. Whatever that retry gets back — a
redirect off the site, a redirect into a path the crawl leaves out, a plain 404 — the page has
an answer and the crawl must move on (scenarios FR-01 to FR-03). Before, such a page was
re-queued every 15 s for ever, logging "backing off 15s before retry (1 URL(s))".
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
    VirtualClock,
)

from app.connectors.sources.web.budget import MAX_RUN_HOURS_ENV
from app.connectors.sources.web.connector import RetryUrl

RETRIED = "http://site.test/retried"
FINE = "http://site.test/fine"
EXCLUDED = "http://site.test/excluded/page"
# A run that loops instead of ending is cut off here and fails the test, instead of hanging CI.
RUN_LIMIT_SECONDS = 20


def _backs_off_until_queued_for_retry(site: FakeWeb, connector, status: int, final: Page) -> None:
    """The page asks to back off until the connector gives up on this sync's first try and queues
    a retry; the retry then gets ``final``. However many requests one try makes, the retry is reached."""
    site.add(RETRIED, Page(status=status))
    validate = connector._validate_fetch_result

    async def switch_once_queued(url: str, *args: object) -> object:
        result = await validate(url, *args)
        if url == RETRIED and RETRIED in connector.retry_urls:
            site.add(RETRIED, final)
        return result

    connector._validate_fetch_result = switch_once_queued


async def _sync(connector) -> None:
    try:
        await asyncio.wait_for(connector.run_sync(), RUN_LIMIT_SECONDS)
    except asyncio.TimeoutError:
        pytest.fail("the sync never ended")


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize(
    "final",
    [
        pytest.param(Page(status=302, location="http://elsewhere.test/landing", content_type=None), id="off-site"),
        pytest.param(Page(status=302, location=EXCLUDED, content_type=None), id="excluded-path"),
        pytest.param(Page(status=404, body=b"gone"), id="not-found"),
    ],
)
async def test_a_retried_page_with_any_final_answer_ends_the_sync(
    site: FakeWeb, make_connector: MakeConnector, db: FakeRecordsDb, clock: VirtualClock,
    status: int, final: Page,
) -> None:
    site.html(START_URL, "Home", RETRIED, FINE)
    site.html(FINE, "Fine")
    site.html(EXCLUDED, "Excluded")
    connector = await make_connector(url_should_contain=["/retried", "/fine"])
    _backs_off_until_queued_for_retry(site, connector, status, final)

    await _sync(connector)

    assert FINE in db.pages()
    assert EXCLUDED not in db.pages()
    assert connector.retry_urls.get(RETRIED) is None or connector.retry_urls[RETRIED].status_code != status, (
        "the retried page's last answer, not the back-off, is what it is remembered by"
    )


async def test_a_page_that_backs_off_on_every_try_ends_as_failed(
    site: FakeWeb, make_connector: MakeConnector, db: FakeRecordsDb,
) -> None:
    site.html(START_URL, "Home", RETRIED, FINE)
    site.html(FINE, "Fine")
    site.add(RETRIED, Page(status=429))
    connector = await make_connector()

    await _sync(connector)

    assert FINE in db.pages()
    assert connector.retry_urls[RETRIED].status_code == 429


def _last_run(checkpoints: FakeCheckpointStore) -> dict:
    (saved,) = checkpoints.sync_points.values()
    return json.loads(saved["lastRun"])


async def test_the_time_limit_ends_the_crawl_and_leaves_unanswered_pages_for_the_next_sync(
    site: FakeWeb, make_connector: MakeConnector, db: FakeRecordsDb, checkpoints: FakeCheckpointStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fetch layer's back-off on the first page uses up the whole (virtual) 18-second deadline.
    monkeypatch.setenv(MAX_RUN_HOURS_ENV, "0.005")
    site.html(START_URL, "Home", RETRIED, FINE)
    site.html(FINE, "Fine")
    site.add(RETRIED, Page(status=429))
    connector = await make_connector()
    connector._crawl_concurrency = 1  # one page at a time, so FINE is only asked for after the deadline

    await _sync(connector)

    run = _last_run(checkpoints)
    assert (run["outcome"], run["stop_reason"]) == ("partial", "time_limit")
    assert FINE not in db.pages(), "nothing is fetched after the deadline"
    assert RETRIED not in db.pages(), "a page still waiting for its retry is not shown as failed"
    assert "timestamp" in next(iter(checkpoints.sync_points.values())), "a partial run still counts as a sync"


async def test_the_report_says_when_the_page_limit_stopped_the_crawl(
    site: FakeWeb, make_connector: MakeConnector, checkpoints: FakeCheckpointStore,
) -> None:
    pages = [f"http://site.test/p{i}" for i in range(5)]
    site.html(START_URL, "Home", *pages)
    for page in pages:
        site.html(page, page)
    connector = await make_connector(max_pages=3)

    await _sync(connector)

    run = _last_run(checkpoints)
    assert (run["outcome"], run["stop_reason"]) == ("completed", "max_pages")
    assert run["pages_visited"] == 3


async def test_a_crawl_that_runs_out_of_pages_reports_no_stop_reason(
    site: FakeWeb, make_connector: MakeConnector, checkpoints: FakeCheckpointStore,
) -> None:
    site.html(START_URL, "Home", FINE, "http://site.test/missing")
    site.html(FINE, "Fine")
    connector = await make_connector()

    await _sync(connector)

    run = _last_run(checkpoints)
    assert (run["outcome"], run["stop_reason"]) == ("completed", None)
    assert run["pages_visited"] == 2, "a page that failed is reported as failed, not as visited"
    assert run["failed_by_status"] == {"404": 1}
    assert run["finished_at"] >= run["started_at"]


async def test_a_stopped_crawl_keeps_its_report_but_not_a_checkpoint(
    site: FakeWeb, make_connector: MakeConnector, checkpoints: FakeCheckpointStore,
) -> None:
    site.html(START_URL, "Home", RETRIED)
    site.add(RETRIED, Page(status=429))
    connector = await make_connector()

    task = asyncio.ensure_future(connector.run_sync())
    while site.gets(RETRIED) < 2:  # noqa: ASYNC110 - the fake site has no event to wait on
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)  # the report is saved shielded from the cancel

    (saved,) = checkpoints.sync_points.values()
    assert "timestamp" not in saved
    assert json.loads(saved["lastRun"])["outcome"] == "stopped"


async def test_a_retry_left_untouched_by_validation_is_dropped(
    make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard behind FR-01..03: any path that forgets a retried URL can't make the crawl loop."""
    connector = await make_connector()
    connector.retry_urls[RETRIED] = RetryUrl(url=RETRIED, status="PENDING", status_code=429, retries=0, last_attempted=0)

    async def forgets(*_: object) -> None:
        return None

    monkeypatch.setattr(connector, "_validate_fetch_result", forgets)

    assert await connector._validate_attempt(RETRIED, 1, None, None) is None
    assert RETRIED not in connector.retry_urls


async def test_a_sync_that_fails_before_crawling_does_not_report_the_previous_run_s_counts(
    site: FakeWeb, make_connector: MakeConnector,
) -> None:
    site.html(START_URL, "Home", FINE)
    site.html(FINE, "Fine")
    connector = await make_connector()
    await connector.run_sync()
    assert connector.last_sync_summary and connector.last_sync_summary["pagesSaved"] > 0

    async def settings_unreadable() -> None:
        raise ValueError("Web connector configuration not found")

    connector.reload_config = settings_unreadable
    with pytest.raises(ValueError):
        await connector.run_sync()

    assert (connector.last_sync_summary or {}).get("pagesSaved", 0) == 0


async def test_a_stop_while_page_processing_lags_behind_the_fetches_ends_the_sync(
    site: FakeWeb, make_connector: MakeConnector, checkpoints: FakeCheckpointStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA-3: fetched pages waiting for processing filled the queue between the two, so on a stop the
    fetching side waited for room that never came, and the sync never ended."""
    pages = [f"/p{i}" for i in range(8)]
    site.html(START_URL, "Home", *pages)
    for page in pages:
        site.html(f"http://site.test{page}", page)
    connector = await make_connector()
    connector.batch_size = 1  # a queue of two pages
    never = asyncio.Event()

    async def stuck(*_: object) -> bool:
        await never.wait()
        return False

    monkeypatch.setattr(connector, "_consume", stuck)
    task = asyncio.ensure_future(connector.run_sync())
    while len(site.fetched_urls()) < 4:  # noqa: ASYNC110 - the fake site has no event to wait on
        await asyncio.sleep(0)
    for _ in range(20):  # let the fetching side block on the full queue
        await asyncio.sleep(0)
    task.cancel()

    done, _ = await asyncio.wait({task}, timeout=5)
    assert done, "the stopped sync never ended"
    assert task.cancelled()
    await asyncio.sleep(0.05)  # the report is saved shielded from the cancel
    assert _last_run(checkpoints)["outcome"] == "stopped"
