"""Re-crawls ask the site whether a stored file changed (ETag / Last-Modified) instead of downloading it again.

A page whose links the crawl still needs is always fetched in full: a "not modified" answer has no body to
read links from. Everything else falls back to comparing content hashes, as before.
"""

import pytest
from web_behaviour_fakes import START_URL, FakeRecordsDb, FakeWeb, MakeConnector, Page

PDF = "http://site.test/manual.pdf"
LAST_MODIFIED = "Wed, 01 Jul 2026 10:00:00 GMT"


@pytest.mark.parametrize(
    "validators",
    [{"etag": '"v1"'}, {"last_modified": LAST_MODIFIED}],
    ids=["etag", "last-modified"],
)
async def test_an_unchanged_document_is_not_downloaded_again(
    validators: dict, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/manual.pdf")
    site.add(PDF, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", **validators))
    connector = await make_connector()
    await connector.run_sync()
    first = db.pages()[PDF]
    uploads = list(site.storage_uploads)

    await connector.run_sync()

    assert site.not_modified == [PDF]
    again = db.pages()[PDF]
    assert (again.id, again.external_revision_id, again.version) == (first.id, first.external_revision_id, first.version)
    assert site.storage_uploads == uploads and site.storage_buffer_updates == []
    assert db.content_updates == []


async def test_a_changed_document_is_downloaded_and_re_indexed(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/manual.pdf")
    site.add(PDF, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector()
    await connector.run_sync()

    site.add(PDF, Page(body=b"%PDF-1.4 v2", content_type="application/pdf", etag='"v2"'))
    await connector.run_sync()

    assert site.not_modified == []
    assert [r.weburl for r in db.content_updates] == [PDF]
    assert db.pages()[PDF].etag == '"v2"'


async def test_a_page_whose_links_are_needed_is_always_fetched_in_full(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.add(START_URL, Page(body=b'<html><body><a href="/leaf">leaf</a></body></html>', etag='"home"'))
    site.add("http://site.test/leaf", Page(body=b"<html><body>Leaf</body></html>", etag='"leaf"'))
    connector = await make_connector(depth=1)
    await connector.run_sync()

    await connector.run_sync()

    assert site.not_modified == ["http://site.test/leaf"]
    assert set(db.pages()) == {START_URL, "http://site.test/leaf"}


async def test_robust_mode_asks_before_downloading_a_document_again(
    browser: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    browser.html(START_URL, "Home", "/manual.pdf")
    browser.add(PDF, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector(use_headless_browser=True)
    await connector.run_sync()
    uploads = list(browser.storage_uploads)

    await connector.run_sync()

    assert browser.not_modified == [PDF]
    assert browser.storage_uploads == uploads
    assert db.pages()[PDF].etag == '"v1"'
