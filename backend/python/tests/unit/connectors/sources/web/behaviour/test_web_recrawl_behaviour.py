"""What a second sync does with pages the first one stored.

Unchanged pages must be left alone, changed ones re-indexed, and a page that
could not be read this time must keep what we already have: a failed read is
never evidence that the page is gone.
"""

import pytest
from web_behaviour_fakes import (
    START_URL,
    FakeCheckpointStore,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
    html_page,
)

from app.config.constants.arangodb import ProgressStatus
from app.connectors.sources.web.connector import WebConnector

GUIDE = "http://site.test/guide"


def _site_with_guide(site: FakeWeb, guide_text: str = "Install with pip") -> None:
    site.html(START_URL, "Home", "/guide")
    site.html(GUIDE, "Guide", text=guide_text)


async def _first_sync(site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector, **kw: object) -> WebConnector:
    _site_with_guide(site)
    connector = await make_connector(**kw)
    await connector.run_sync()
    db.mark_indexed()
    db.new_batches.clear()
    return connector


async def test_an_unchanged_page_is_not_re_uploaded_or_re_indexed(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)
    first = db.pages()[GUIDE]
    uploads = len(site.storage_uploads)

    await connector.run_sync()

    again = db.pages()[GUIDE]
    assert (again.id, again.version, again.external_revision_id) == (first.id, first.version, first.external_revision_id)
    assert again.indexing_status == ProgressStatus.COMPLETED.value
    assert db.content_updates == [] and db.metadata_updates == []
    assert len(site.storage_uploads) == uploads
    assert site.storage_buffer_updates == []


async def test_changed_page_text_is_re_indexed_in_place(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)
    first = db.pages()[GUIDE]

    site.html(GUIDE, "Guide", text="Install with uv instead")
    await connector.run_sync()

    again = db.pages()[GUIDE]
    assert [r.weburl for r in db.content_updates] == [GUIDE]
    assert again.id == first.id
    assert again.version == first.version + 1
    assert again.external_revision_id != first.external_revision_id
    assert again.storage_document_id == first.storage_document_id
    assert site.storage_buffer_updates == [first.storage_document_id]
    assert b"Install with uv instead" in site.storage_docs[first.storage_document_id]


async def test_a_new_title_renames_the_stored_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)

    site.add(GUIDE, Page(body=html_page("Guide", text="Install with pip").replace(b"<title>Guide", b"<title>Setup guide")))
    await connector.run_sync()

    assert [r.record_name for r in db.metadata_updates] == ["Setup guide"]
    assert db.pages()[GUIDE].record_name == "Setup guide"


async def test_markup_only_changes_do_not_count_as_a_content_change(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)

    body = html_page("Guide", text="Install with pip").replace(b"<p>", b'<p class="lead" data-build="42">')
    site.add(GUIDE, Page(body=body.replace(b"</body>", b"<script>var build=43;</script></body>")))
    await connector.run_sync()

    assert db.content_updates == []


@pytest.mark.parametrize(
    "outage",
    [
        pytest.param(Page(status=503, body=b"down"), id="503"),
        pytest.param(Page(status=500, body=b"boom"), id="500"),
        pytest.param(Page(status=429, body=b"slow down"), id="429"),
        pytest.param(Page(status=403, body=b"blocked"), id="403"),
    ],
)
async def test_a_page_that_fails_to_load_keeps_its_stored_record(
    outage: Page, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)
    first = db.pages()[GUIDE]

    site.add(GUIDE, outage)
    await connector.run_sync()
    await connector.run_sync()

    kept = db.pages()[GUIDE]
    assert db.deleted == []
    assert (kept.id, kept.external_revision_id, kept.storage_document_id) == (
        first.id, first.external_revision_id, first.storage_document_id,
    )
    assert kept.indexing_status == ProgressStatus.COMPLETED.value
    assert kept.reason is None
    assert db.content_updates == [] and db.new_batches == []


async def test_a_site_that_is_unreachable_keeps_every_stored_record(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)
    before = {url: (r.id, r.external_revision_id) for url, r in db.pages().items()}

    site.add(START_URL, Page(status=503, body=b""))
    site.add(GUIDE, Page(status=503, body=b""))
    await connector.run_sync()
    await connector.run_sync()

    assert {url: (r.id, r.external_revision_id) for url, r in db.pages().items()} == before
    assert db.deleted == []


async def test_a_page_the_start_page_stops_linking_to_is_kept(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)

    site.html(START_URL, "Home")
    await connector.run_sync()

    assert GUIDE in db.pages()
    assert db.deleted == []


@pytest.mark.parametrize("status", [404, 410])
async def test_a_page_gone_on_two_syncs_in_a_row_is_removed_with_its_stored_copy(
    status: int, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)
    first = db.pages()[GUIDE]

    site.add(GUIDE, Page(status=status, body=b"gone"))
    await connector.run_sync()
    assert GUIDE in db.pages() and db.deleted == []

    await connector.run_sync()

    assert db.deleted == [first.id]
    assert site.storage_deletes == [first.storage_document_id]
    # Still linked from the site, so it stays listed as a failed page with nothing indexed.
    remains = db.pages()[GUIDE]
    assert remains.id != first.id and remains.storage_document_id is None
    assert remains.indexing_status == ProgressStatus.FAILED.value
    assert START_URL in db.pages()


async def test_a_page_gone_once_and_then_back_starts_counting_again(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)

    for page in (Page(status=404), html_page("Guide", text="Install with pip"), Page(status=404)):
        site.add(GUIDE, page if isinstance(page, Page) else Page(body=page))
        await connector.run_sync()

    assert GUIDE in db.pages()
    assert db.deleted == []


async def test_nothing_is_removed_while_the_start_page_itself_is_gone(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)

    site.add(START_URL, Page(status=404))
    site.add(GUIDE, Page(status=404))
    await connector.run_sync()
    await connector.run_sync()

    assert db.deleted == []
    assert {START_URL, GUIDE} <= set(db.pages())


async def test_a_page_that_failed_last_time_is_indexed_once_it_loads(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/guide")
    site.add(GUIDE, Page(status=503, body=b""))
    connector = await make_connector()
    await connector.run_sync()
    placeholder = db.pages()[GUIDE]
    assert placeholder.indexing_status == ProgressStatus.FAILED.value

    site.html(GUIDE, "Guide", text="Now it works")
    await connector.run_sync()

    recovered = db.pages()[GUIDE]
    assert recovered.id == placeholder.id
    assert recovered.indexing_status != ProgressStatus.FAILED.value
    assert recovered.reason is None
    assert recovered.storage_document_id in site.storage_docs
    assert b"Now it works" in site.storage_docs[recovered.storage_document_id]


async def test_the_sync_checkpoint_is_not_written_when_the_crawl_crashes(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    _site_with_guide(site)
    connector = await make_connector()
    db.fail_writes = True

    with pytest.raises(RuntimeError, match="records database unavailable"):
        await connector.run_sync()

    assert checkpoints.sync_points == {}


async def test_pages_that_failed_are_fetched_again_on_the_next_sync(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/guide")
    site.add(GUIDE, Page(status=503, body=b""))
    connector = await make_connector()
    await connector.run_sync()
    assert checkpoints.writes == 1
    gets_after_first = site.gets(GUIDE)

    await connector.run_sync()

    assert site.gets(GUIDE) > gets_after_first


async def test_storage_outage_still_records_the_page_for_live_fetch(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    _site_with_guide(site)
    site.storage_down = True
    connector = await make_connector()

    await connector.run_sync()

    guide = db.pages()[GUIDE]
    assert guide.storage_document_id is None
    assert guide.weburl == GUIDE


@pytest.mark.parametrize("robust", [False, True], ids=["plain", "robust-mode"])
async def test_a_page_that_moved_leaves_no_stale_record_under_its_old_url(
    robust: bool, browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    old, new = "http://site.test/old-name", "http://site.test/new-name"
    browser.html(START_URL, "Home", "/old-name")
    browser.html(old, "Guide")
    connector = await make_connector(use_headless_browser=robust)
    await connector.run_sync()
    stale = db.pages()[old]

    browser.redirect(old, "/new-name", status=301)
    browser.html(new, "Guide")
    await connector.run_sync()
    assert old in db.pages()

    await connector.run_sync()

    assert db.deleted == [stale.id]
    assert browser.storage_deletes == [stale.storage_document_id]
    assert set(db.pages()) == {START_URL, new}


async def test_a_redirect_off_the_site_never_removes_the_stored_page(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    connector = await _first_sync(site, db, make_connector)

    site.redirect(GUIDE, "http://other.test/guide")
    site.html("http://other.test/guide", "Guide elsewhere")
    await connector.run_sync()
    await connector.run_sync()

    assert db.deleted == []
    assert GUIDE in db.pages()


@pytest.mark.parametrize(
    ("link", "stored_key"),
    [
        pytest.param("/docs/", "http://site.test/docs/", id="trailing-slash"),
        pytest.param("/list?b=2&a=1", "http://site.test/list?b=2&a=1", id="query-in-the-site-s-order"),
    ],
)
async def test_a_stored_page_gone_twice_is_removed_whatever_key_it_was_stored_under(
    link: str, stored_key: str, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    url = f"http://site.test{link}"
    site.html(START_URL, "Home", link)
    site.html(url, "Page")
    connector = await make_connector()
    await connector.run_sync()
    [key] = [k for k in db.records if k.startswith(url.split("?")[0].rstrip("/"))]
    stored = db.records.pop(key)
    stored.external_record_id = stored_key  # as an older version of the connector keyed it
    db.records[stored_key] = stored

    site.add(url, Page(status=404))
    await connector.run_sync()
    # The first 404 finds the stored record, so no failed copy is added beside it.
    assert [r.id for r in db.records.values() if r.weburl != START_URL] == [stored.id]
    await connector.run_sync()

    assert db.deleted == [stored.id]
    # Still linked from the site, so it stays listed as one failed page with nothing indexed.
    [remains] = [r for r in db.records.values() if r.weburl != START_URL]
    assert remains.indexing_status == ProgressStatus.FAILED.value and remains.storage_document_id is None
