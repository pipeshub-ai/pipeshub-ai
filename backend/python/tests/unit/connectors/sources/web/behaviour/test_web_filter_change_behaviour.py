"""Narrowing the file-type filter after a crawl removes what it now leaves out.

Saving the filters clears the connector's sync points, so the next run is a
full crawl with the new filters. A page it fetches and the filter excludes
leaves every store; a page it could not fetch is left as it is.
"""

from web_behaviour_fakes import (
    START_URL,
    FakeCheckpointStore,
    FakeRecordsDb,
    FakeWeb,
    MakeConnector,
    Page,
)

NOTES = "http://site.test/notes.txt"
ABOUT = "http://site.test/about"


def _exclude(*extensions: str) -> dict:
    return {"sync": {"values": {"file_extensions": {"operator": "not_in", "value": list(extensions), "type": "multiselect"}}}}


async def _crawl_then_narrow(site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore,
                             make_connector: MakeConnector) -> object:
    site.html(START_URL, "Home", "/notes.txt", "/about")
    site.add(NOTES, Page(body=b"meeting notes", content_type="text/plain; charset=utf-8"))
    site.html("http://site.test/about", "About")
    connector = await make_connector()
    await connector.run_sync()
    assert NOTES in db.pages()

    connector.config_service.filters = _exclude("txt")
    checkpoints.sync_points.clear()
    return connector


async def test_a_narrowed_filter_removes_the_file_it_now_leaves_out(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    connector = await _crawl_then_narrow(site, db, checkpoints, make_connector)
    notes = db.pages()[NOTES]

    await connector.run_sync()

    assert db.deleted == [notes.id]
    assert set(db.pages()) == {START_URL, ABOUT}
    assert notes.storage_document_id not in site.storage_docs, "its stored copy goes too"


async def test_a_file_the_filter_leaves_out_but_that_cannot_be_fetched_is_kept(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    connector = await _crawl_then_narrow(site, db, checkpoints, make_connector)
    site.add(NOTES, Page(status=503, body=b"down", content_type="text/plain"))

    await connector.run_sync()

    assert db.deleted == []
    assert NOTES in db.pages()


async def test_an_unchanged_filter_removes_nothing(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/notes.txt", "/about")
    site.add(NOTES, Page(body=b"meeting notes", content_type="text/plain; charset=utf-8"))
    site.html("http://site.test/about", "About")
    connector = await make_connector()
    await connector.run_sync()

    checkpoints.sync_points.clear()
    await connector.run_sync()

    assert db.deleted == []
    assert set(db.pages()) == {START_URL, ABOUT, NOTES}


async def test_a_single_page_crawl_removes_its_page_once_the_filter_leaves_it_out(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    site.add(START_URL, Page(body=b"plain text start", content_type="text/plain; charset=utf-8"))
    connector = await make_connector(crawl_type="single")
    await connector.run_sync()
    assert START_URL in db.pages()

    connector.config_service.filters = _exclude("txt")
    checkpoints.sync_points.clear()
    await connector.run_sync()

    assert db.pages() == {}


def _only(*extensions: str) -> dict:
    return {"sync": {"values": {"file_extensions": {"operator": "in", "value": list(extensions), "type": "multiselect"}}}}


async def test_a_not_modified_extensionless_page_is_classified_from_its_stored_copy_under_not_in(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    # The 304 carries no Content-Type and the URL no extension, so only the stored
    # record says this page is text; read as html, "not in txt" would keep it.
    site.add(START_URL, Page(body=b"plain text start", content_type="text/plain; charset=utf-8", etag='"v1"'))
    connector = await make_connector(crawl_type="single")
    await connector.run_sync()
    assert START_URL in db.pages()

    connector.config_service.filters = _exclude("txt")
    checkpoints.sync_points.clear()
    await connector.run_sync()

    assert site.not_modified == [START_URL]
    assert db.pages() == {}


async def test_a_not_modified_extensionless_page_is_classified_from_its_stored_copy_under_in(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    # Read as html, "in pdf" would delete a PDF the 304 says is unchanged.
    site.add(START_URL, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector(crawl_type="single")
    await connector.run_sync()
    kept = db.pages()[START_URL]

    connector.config_service.filters = _only("pdf")
    checkpoints.sync_points.clear()
    await connector.run_sync()

    assert site.not_modified == [START_URL]
    assert db.deleted == []
    assert db.pages()[START_URL].id == kept.id


async def test_a_url_redirecting_to_a_page_the_filter_leaves_out_loses_its_record_too(
    site: FakeWeb, db: FakeRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    # A landing the filter drops is never kept, so the redirect cleanup a kept landing
    # gets never runs; the old name is a text file the filter leaves out too, so it goes.
    connector = await _crawl_then_narrow(site, db, checkpoints, make_connector)
    site.redirect(NOTES, "/moved.txt", status=301)
    site.add("http://site.test/moved.txt", Page(body=b"meeting notes", content_type="text/plain"))

    await connector.run_sync()

    assert NOTES not in db.pages()
    assert set(db.pages()) == {START_URL, ABOUT}
