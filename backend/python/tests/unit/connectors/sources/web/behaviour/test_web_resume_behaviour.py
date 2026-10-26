"""An unfinished crawl carries on where it stopped (scenario DI-09).

Before, every sync started again from the start page, so a crawl that couldn't finish in one go
(rate limits, a restart, a stop) redid the same first pages for ever and never reached the rest.
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
)

from app.config.constants.arangodb import ProgressStatus
from app.connectors.sources.web import connector as connector_module
from app.connectors.sources.web.budget import MAX_RUN_HOURS_ENV
from app.connectors.sources.web.connector import MAX_RETRIES

PAGES = [f"http://site.test/p{i}" for i in range(6)]
SLOW = "http://site.test/slow"


def _site(site: FakeWeb) -> None:
    site.html(START_URL, "Home", "/slow", *(p.removeprefix("http://site.test") for p in PAGES))
    for page in PAGES:
        site.html(page, page)


def _saved(checkpoints: FakeCheckpointStore) -> dict:
    (saved,) = checkpoints.sync_points.values()
    return saved


@pytest.fixture
def checkpoint_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connector_module, "CHECKPOINT_EVERY_PAGES", 1)


async def test_a_crawl_that_ran_out_of_time_carries_on_without_redoing_its_pages(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _site(site)
    site.add(SLOW, Page(status=429))  # its back-off uses up the first sync's (virtual) time
    monkeypatch.setenv(MAX_RUN_HOURS_ENV, "0.005")
    connector = await make_connector()
    await connector.run_sync()
    assert json.loads(_saved(checkpoints)["lastRun"])["outcome"] == "partial"
    assert _saved(checkpoints)["resume"]
    start_fetches = site.gets(START_URL)

    monkeypatch.delenv(MAX_RUN_HOURS_ENV)
    site.html(SLOW, "Slow")
    await connector.run_sync()

    assert site.gets(START_URL) == start_fetches, "the start page was done last time"
    assert {*PAGES, SLOW} <= set(db.pages())
    saved = _saved(checkpoints)
    assert json.loads(saved["lastRun"])["resumed"] is True
    assert saved["resume"] == "", "a finished crawl leaves nothing to resume"


async def test_a_stopped_crawl_resumes_from_its_last_checkpoint(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    checkpoint_every_page: None,
) -> None:
    _site(site)
    site.add(SLOW, Page(status=429))
    connector = await make_connector()
    task = asyncio.ensure_future(connector.run_sync())
    while site.gets(SLOW) < 2:  # noqa: ASYNC110 - the fake site has no event to wait on
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert _saved(checkpoints)["resume"]
    first = {url: r.id for url, r in db.pages().items()}

    site.html(SLOW, "Slow")
    await connector.run_sync()

    assert {*PAGES, SLOW} <= set(db.pages())
    assert {url: db.pages()[url].id for url in first} == first


async def test_pages_fetched_but_not_written_before_a_failure_are_done_on_resume(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    checkpoint_every_page: None,
) -> None:
    """The checkpoint never counts a page as done before its record is written."""
    _site(site)
    connector = await make_connector()
    db.fail_writes = True
    with pytest.raises(RuntimeError):
        await connector.run_sync()
    assert db.pages() == {}

    db.fail_writes = False
    await connector.run_sync()

    assert {START_URL, *PAGES} <= set(db.pages())


async def test_a_checkpoint_taken_under_other_settings_is_not_used(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _site(site)
    site.add(SLOW, Page(status=429))
    monkeypatch.setenv(MAX_RUN_HOURS_ENV, "0.005")
    connector = await make_connector()
    await connector.run_sync()
    start_fetches = site.gets(START_URL)

    monkeypatch.delenv(MAX_RUN_HOURS_ENV)
    connector.config_service.sync["depth"] = 2  # read again at the next sync
    await connector.run_sync()

    assert site.gets(START_URL) == start_fetches + 1, "a different crawl starts from the start page"
    assert json.loads(_saved(checkpoints)["lastRun"])["resumed"] is False


async def test_a_short_database_blip_does_not_fail_the_sync(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
) -> None:
    """RS-16: a failed write is tried again before the sync gives up."""
    _site(site)
    connector = await make_connector()
    write = db.on_new_records
    failures = [1]

    async def flaky(pairs: list) -> None:
        if failures[0]:
            failures[0] -= 1
            raise RuntimeError("records database unavailable")
        await write(pairs)

    db.on_new_records = flaky
    await connector.run_sync()

    assert {START_URL, *PAGES} <= set(db.pages())
    assert json.loads(_saved(checkpoints)["lastRun"])["outcome"] == "completed"


async def test_progress_is_saved_through_a_run_of_failed_pages(
    site: FakeWeb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The time-based checkpoint fires even when no page is kept: before, it was only checked after one."""
    monkeypatch.setattr(connector_module, "CHECKPOINT_EVERY_PAGES", 10**6)
    monkeypatch.setattr(connector_module, "CHECKPOINT_EVERY_SECONDS", 0)
    site.html(START_URL, "Home", *(p.removeprefix("http://site.test") for p in PAGES))  # all 404
    connector = await make_connector()
    fetched_at_save: list[int] = []
    save = checkpoints.update_sync_point

    async def spy(key: str, data: dict) -> None:
        if data.get("resume"):
            fetched_at_save.append(len(site.fetched_urls()))
        await save(key, data)

    checkpoints.update_sync_point = spy
    await connector.run_sync()

    assert max(fetched_at_save) == 1 + len(PAGES)


async def test_a_page_that_failed_before_a_stop_is_recorded_as_failed_on_resume(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before, the checkpoint left out pages already given up on, so the resumed crawl never wrote their record."""
    monkeypatch.setattr(connector_module, "CHECKPOINT_EVERY_SECONDS", 0)
    broken = "http://site.test/broken"
    _site(site)
    site.html(START_URL, "Home", "/slow", "/broken", *(p.removeprefix("http://site.test") for p in PAGES))
    site.add(SLOW, Page(status=429))
    site.add(broken, Page(status=401))
    connector = await make_connector()
    task = asyncio.ensure_future(connector.run_sync())
    while not (  # noqa: ASYNC110 - the fake site has no event to wait on
        (given_up := connector.retry_urls.get(broken)) and given_up.retries >= MAX_RETRIES and site.gets(SLOW) >= 2
    ):
        await asyncio.sleep(0)
    for _ in range(20):  # let the round end and its checkpoint be saved
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)
    assert broken not in db.pages()

    site.html(SLOW, "Slow")
    await connector.run_sync()

    assert json.loads(_saved(checkpoints)["lastRun"])["resumed"] is True
    assert db.pages()[broken].indexing_status == ProgressStatus.FAILED.value


async def test_a_sync_that_fails_before_it_starts_crawling_keeps_the_last_checkpoint(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _site(site)
    site.add(SLOW, Page(status=429))
    monkeypatch.setenv(MAX_RUN_HOURS_ENV, "0.005")
    connector = await make_connector()
    await connector.run_sync()
    resume = _saved(checkpoints)["resume"]
    assert resume
    start_fetches = site.gets(START_URL)

    monkeypatch.delenv(MAX_RUN_HOURS_ENV)
    site.html(SLOW, "Slow")

    async def unreadable(*_: object, **__: object) -> list:
        raise RuntimeError("records database unavailable")

    readable = db.get_records_in_record_group
    monkeypatch.setattr(db, "get_records_in_record_group", unreadable)
    with pytest.raises(RuntimeError):
        await connector.run_sync()
    assert json.loads(_saved(checkpoints)["lastRun"])["outcome"] == "failed"
    assert _saved(checkpoints)["resume"] == resume

    monkeypatch.setattr(db, "get_records_in_record_group", readable)
    await connector.run_sync()
    assert site.gets(START_URL) == start_fetches, "the crawl carried on from the checkpoint"
    assert {*PAGES, SLOW} <= set(db.pages())


async def test_a_sync_that_fails_before_crawling_after_a_finished_one_leaves_nothing_to_resume(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _site(site)
    connector = await make_connector()
    await connector.run_sync()
    assert _saved(checkpoints)["resume"] == ""

    async def unavailable(*_: object, **__: object) -> None:
        raise RuntimeError("records database unavailable")

    # The same connector object is reused: its last frontier must not become this run's checkpoint.
    monkeypatch.setattr(db, "on_new_app_users", unavailable)
    monkeypatch.setattr(db, "ensure_team_app_edge", unavailable)
    with pytest.raises(RuntimeError):
        await connector.run_sync()

    assert json.loads(_saved(checkpoints)["lastRun"])["outcome"] == "failed"
    assert _saved(checkpoints)["resume"] == ""


async def test_a_resume_left_with_only_pages_that_keep_failing_starts_over_and_sees_site_changes(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scheduled crawl got stuck resuming the same failing pages each run, so it never revisited the
    site: edited pages were never re-indexed and new ones never found."""
    _site(site)
    site.add(SLOW, Page(status=429))  # never answers, and its back-off uses up each sync's time
    monkeypatch.setenv(MAX_RUN_HOURS_ENV, "0.005")
    connector = await make_connector()
    await connector.run_sync()
    assert _saved(checkpoints)["resume"], "the first sync ran out of time, so it carries on"
    db.mark_indexed()
    # The start page is the one page the first sync surely stored; its links stay as they were.
    edited, start_fetches = START_URL, site.gets(START_URL)
    assert edited in db.pages()
    site.html(START_URL, "Home", "/slow", *(p.removeprefix("http://site.test") for p in PAGES),
              text="The start page changed since the first sync")

    # Each sync resumes while it gets somewhere; once only the failing page is left, it starts over.
    for _ in range(4):
        await connector.run_sync()
        if site.gets(START_URL) > start_fetches:
            break

    assert site.gets(START_URL) > start_fetches, "the crawl started over from the start page"
    assert edited in [r.weburl for r in db.content_updates], "the edited page was re-indexed"


async def test_a_rate_limit_stop_keeps_the_pages_it_never_reached_for_the_next_sync(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
) -> None:
    """A resume that reached no new page is still progress while pages it never fetched are queued."""
    limited = [f"http://site.test/busy-{i}" for i in range(30)]
    later = "http://site.test/later"
    site.html(START_URL, "Home", *(u.removeprefix("http://site.test") for u in limited), "/later")
    for url in limited:
        site.add(url, Page(status=429))
    site.html(later, "Later")
    connector = await make_connector()
    await connector.run_sync()
    first = json.loads(_saved(checkpoints)["lastRun"])
    assert (first["outcome"], first["stop_reason"]) == ("partial", "rate_limited")
    assert site.gets(later) == 0, "the rate limit stopped the crawl before it got there"

    await connector.run_sync()  # resumes; the queued pages still answer 429
    second = json.loads(_saved(checkpoints)["lastRun"])
    assert (second["resumed"], second["stop_reason"]) == (True, "rate_limited")
    assert _saved(checkpoints)["resume"], "pages it never fetched are still to do"

    for url in limited:
        site.html(url, url)
    start_fetches = site.gets(START_URL)
    await connector.run_sync()

    assert later in db.pages()
    assert site.gets(START_URL) == start_fetches, "it carried on instead of starting over"
