"""Re-crawls ask the site whether a stored file changed (ETag / Last-Modified) instead of downloading it again.

A page whose links the crawl still needs is always fetched in full: a "not modified" answer has no body to
read links from. Everything else falls back to comparing content hashes, as before.
"""

import pytest
from web_behaviour_fakes import START_URL, FakeRecordsDb, FakeWeb, MakeConnector, Page

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.sources.web.connector import WebConnector
from app.models.entities import FileRecord, RecordType

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


async def test_a_changed_document_whose_answer_drops_its_etag_does_not_keep_the_old_one(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/manual.pdf")
    site.add(PDF, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector()
    await connector.run_sync()

    site.add(PDF, Page(body=b"%PDF-1.4 v2", content_type="application/pdf"))
    await connector.run_sync()
    assert db.pages()[PDF].etag is None

    # A later answer tagged "v1" again must not be taken as vouching for the stored v2 copy.
    site.add(PDF, Page(body=b"%PDF-1.4 v3", content_type="application/pdf", etag='"v1"'))
    await connector.run_sync()

    assert site.not_modified == []
    assert site.storage_docs[db.pages()[PDF].storage_document_id] == b"%PDF-1.4 v3"


async def test_a_record_migrated_from_an_older_address_does_not_keep_validators_for_changed_content(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    # Stored by an older version without the trailing slash; this sync migrates it to "/guide/".
    guide = "http://site.test/guide"
    db.records[guide] = FileRecord(
        id="legacy-1", org_id="org-1", record_name="Guide", record_type=RecordType.FILE,
        external_record_id=guide, version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.WEB,
        connector_id="web-1", weburl=guide, is_file=True, mime_type=MimeTypes.HTML.value,
        external_revision_id="hash-of-the-old-copy", storage_document_id="old-copy",
        etag='"v1"', ctag=LAST_MODIFIED,
    )
    site.html(START_URL, "Home", "/guide")
    site.html(guide, "Guide", text="Rewritten since the old copy")

    await (await make_connector()).run_sync()

    migrated = next(r for r in db.records.values() if r.id == "legacy-1")
    assert migrated.external_record_id == guide + "/"
    assert (migrated.etag, migrated.ctag) == (None, None)


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


@pytest.mark.parametrize(
    ("before", "after"),
    [({}, {"etag": '"v1"'}), ({"etag": '"v1"'}, {"etag": '"v1-rotated"'}), ({}, {"last_modified": LAST_MODIFIED})],
    ids=["gained-etag", "rotated-etag", "gained-last-modified"],
)
async def test_new_validators_on_an_unchanged_file_are_saved_for_the_next_sync(
    before: dict, after: dict, site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/manual.pdf")
    site.add(PDF, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", **before))
    connector = await make_connector()
    await connector.run_sync()
    first = db.pages()[PDF]

    site.add(PDF, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", **after))
    await connector.run_sync()

    again = db.pages()[PDF]
    assert (again.etag, again.ctag) == (after.get("etag"), after.get("last_modified"))
    assert again.version == first.version
    assert db.content_updates == []

    await connector.run_sync()
    assert site.not_modified == [PDF]


async def test_a_validator_the_site_stops_sending_is_kept_while_the_file_is_unchanged(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/manual.pdf")
    site.add(PDF, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector()
    await connector.run_sync()

    site.add(PDF, Page(body=b"%PDF-1.4 v1", content_type="application/pdf"))
    await connector.run_sync()

    assert db.pages()[PDF].etag == '"v1"'


async def test_a_file_that_moved_with_the_same_etag_is_stored_at_its_new_url(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    moved = "http://site.test/files/handbook.pdf"
    old = "http://site.test/handbook.pdf"
    site.html(START_URL, "Home", "/handbook.pdf")
    site.add(old, Page(body=b"%PDF-1.4 handbook", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector()
    await connector.run_sync()
    stale = db.pages()[old]

    site.redirect(old, "/files/handbook.pdf", status=301)
    site.add(moved, Page(body=b"%PDF-1.4 handbook", content_type="application/pdf", etag='"v1"'))
    await connector.run_sync()
    assert site.storage_docs[db.pages()[moved].storage_document_id] == b"%PDF-1.4 handbook"
    uploads, gets = list(site.storage_uploads), site.gets(moved)
    await connector.run_sync()

    # Current at its new URL: one GET answered 304, no refetch, and the old record still cleaned up.
    assert site.not_modified[-1:] == [moved]
    assert site.gets(moved) == gets + 1
    assert site.storage_uploads == uploads
    assert db.deleted == [stale.id]
    assert set(db.pages()) == {START_URL, moved}


async def test_a_file_reached_only_through_a_redirect_is_asked_about_before_downloading(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    moved = "http://site.test/files/handbook.pdf"
    site.html(START_URL, "Home", "/handbook.pdf")
    site.redirect("http://site.test/handbook.pdf", "/files/handbook.pdf", status=301)
    site.add(moved, Page(body=b"%PDF-1.4 handbook", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector()
    await connector.run_sync()
    uploads = list(site.storage_uploads)

    await connector.run_sync()

    assert site.not_modified == [moved]
    assert site.storage_uploads == uploads


async def test_a_304_carrying_the_old_url_s_etag_does_not_vouch_for_an_older_copy_at_the_new_url(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    old, new = "http://site.test/old.pdf", "http://site.test/new.pdf"
    site.html(START_URL, "Home", "/old.pdf", "/new.pdf")
    site.add(old, Page(body=b"%PDF-1.4 v2", content_type="application/pdf", etag='"v2"'))
    site.add(new, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector()
    await connector.run_sync()

    # A site that refuses HEAD: the GET follows the redirect carrying /old.pdf's ETag, which /new.pdf now has.
    site.html(START_URL, "Home", "/old.pdf")
    site.add(old, Page(status=301, location="/new.pdf", content_type=None, head_status=405))
    site.add(new, Page(body=b"%PDF-1.4 v2", content_type="application/pdf", etag='"v2"'))
    await connector.run_sync()

    assert site.storage_docs[db.pages()[new].storage_document_id] == b"%PDF-1.4 v2"


async def test_a_matching_last_modified_does_not_vouch_for_a_copy_whose_etag_differs(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    old, new = "http://site.test/old.pdf", "http://site.test/new.pdf"
    site.html(START_URL, "Home", "/old.pdf", "/new.pdf")
    site.add(old, Page(body=b"%PDF-1.4 v2", content_type="application/pdf", etag='"v2"', last_modified=LAST_MODIFIED))
    site.add(new, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"', last_modified=LAST_MODIFIED))
    connector = await make_connector()
    await connector.run_sync()

    site.html(START_URL, "Home", "/old.pdf")
    site.add(old, Page(status=301, location="/new.pdf", content_type=None, head_status=405))
    site.add(new, Page(body=b"%PDF-1.4 v2", content_type="application/pdf", etag='"v2"', last_modified=LAST_MODIFIED))
    await connector.run_sync()

    assert site.storage_docs[db.pages()[new].storage_document_id] == b"%PDF-1.4 v2"


def _moved_file_whose_304_does_not_match(site: FakeWeb, refetch: Page) -> None:
    """/old.pdf now 301s to /new.pdf; /new.pdf's 304 carries an ETag its stored copy doesn't have,
    so the connector fetches /new.pdf in full, and that fetch answers ``refetch``."""
    site.html(START_URL, "Home", "/old.pdf")
    site.add("http://site.test/old.pdf", Page(status=301, location="/new.pdf", content_type=None, head_status=405))
    site.add("http://site.test/new.pdf", [
        Page(body=b"%PDF-1.4 v2", content_type="application/pdf", etag='"v2"', last_modified=LAST_MODIFIED),
        refetch,
    ])


async def _two_stored_files(site: FakeWeb, make_connector: MakeConnector) -> WebConnector:
    site.html(START_URL, "Home", "/old.pdf", "/new.pdf")
    site.add("http://site.test/old.pdf",
             Page(body=b"%PDF-1.4 v2", content_type="application/pdf", etag='"v2"', last_modified=LAST_MODIFIED))
    site.add("http://site.test/new.pdf",
             Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"', last_modified=LAST_MODIFIED))
    connector = await make_connector()
    await connector.run_sync()
    return connector


async def test_a_moved_file_whose_new_address_is_gone_is_removed_from_its_old_address_too(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    old = "http://site.test/old.pdf"
    connector = await _two_stored_files(site, make_connector)
    stale = db.pages()[old]

    for _ in range(2):  # gone on two syncs in a row
        _moved_file_whose_304_does_not_match(site, Page(status=404, body=b"gone"))
        await connector.run_sync()

    assert stale.id in db.deleted
    assert stale.storage_document_id not in site.storage_docs
    assert old not in db.pages() or not db.pages()[old].storage_document_id


async def test_a_moved_file_whose_new_address_never_answers_keeps_its_old_copy(
    site: FakeWeb, db: FakeRecordsDb, make_connector: MakeConnector
) -> None:
    old = "http://site.test/old.pdf"
    connector = await _two_stored_files(site, make_connector)
    stale = db.pages()[old]

    for _ in range(2):
        _moved_file_whose_304_does_not_match(site, Page(hang_up=True))
        await connector.run_sync()

    assert stale.id not in db.deleted
    assert site.storage_docs[db.pages()[old].storage_document_id] == b"%PDF-1.4 v2"
