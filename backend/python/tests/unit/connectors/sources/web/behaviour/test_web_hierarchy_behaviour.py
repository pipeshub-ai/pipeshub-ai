"""The record tree: each page hangs under the record of its URL-path parent.

The fake records database here links a record to its parent at write time and drops the
links of a deleted record, as the graph stores do (a delete removes the vertex with its
edges, and a record is linked to its parent only when it is written).
"""

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

from app.connectors.sources.web.connector import WebConnector
from app.models.entities import Record

DOCS = "http://site.test/docs/"
CHILD = "http://site.test/docs/a.html"


class LinkingRecordsDb(FakeRecordsDb):
    """FakeRecordsDb plus the parent-child links: child record id -> parent record id."""

    def __init__(self) -> None:
        super().__init__()
        self.parent_of: dict[str, str] = {}

    def _store(self, record: Record) -> None:
        super()._store(record)
        # A write links a record to the parent its reference names, and to no other.
        self.parent_of.pop(record.id, None)
        parent = self.records.get(record.parent_external_record_id or "")
        if parent is not None:
            self.parent_of[record.id] = parent.id

    async def on_records_attached_to_parent(self, parent_record_id: str, record_ids: list[str]) -> None:
        await super().on_records_attached_to_parent(parent_record_id, record_ids)
        self.parent_of.update(dict.fromkeys(record_ids, parent_record_id))

    async def on_record_deleted(self, record_id: str, **kw: object) -> bool:
        in_trash = await super().on_record_deleted(record_id, **kw)
        if not in_trash:
            self.parent_of = {c: p for c, p in self.parent_of.items() if record_id not in (c, p)}
        return in_trash


class FullSyncRecordsDb(LinkingRecordsDb):
    """LinkingRecordsDb plus how a full sync ends: a record the run did not write loses its links,
    those of its children included (``sync_runner._unlink_what_full_sync_missed``)."""

    def __init__(self) -> None:
        super().__init__()
        self.written: set[str] = set()

    def _store(self, record: Record) -> None:
        super()._store(record)
        self.written.add(record.id)

    async def on_updated_record_permissions(self, record: Record, permissions: list) -> None:
        """An unchanged page's permissions update counts as a write, but does not link it to its parent."""
        await super().on_updated_record_permissions(record, permissions)
        self.written.add(record.id)

    def end_full_sync(self) -> None:
        self.parent_of = {c: p for c, p in self.parent_of.items() if c in self.written and p in self.written}
        self.written = set()


@pytest.fixture
def db() -> FullSyncRecordsDb:
    return FullSyncRecordsDb()


def _site(site: FakeWeb) -> None:
    # The child is linked from the start page too, so it stays reachable while its folder page is gone.
    site.html(START_URL, "Home", "/docs/", "/docs/a.html")
    site.html(DOCS, "Docs", "/docs/a.html", text="All the docs")
    site.html(CHILD, "Page A", text="Install with pip")


async def test_a_page_is_linked_under_its_url_path_parent(
    site: FakeWeb, db: LinkingRecordsDb, make_connector: MakeConnector
) -> None:
    _site(site)
    connector = await make_connector()
    await connector.run_sync()

    assert db.parent_of[db.records[CHILD].id] == db.records[DOCS].id


async def test_children_of_a_removed_folder_page_are_linked_again_when_it_comes_back(
    site: FakeWeb, db: LinkingRecordsDb, make_connector: MakeConnector
) -> None:
    _site(site)
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()

    site.add(DOCS, Page(status=404))
    await connector.run_sync()
    await connector.run_sync()  # the second 404 in a row removes the folder page
    assert DOCS not in db.pages() and CHILD in db.pages()
    # While the page is gone, a folder in its place holds the child.
    assert db.records[DOCS].is_internal
    assert db.parent_of.get(db.records[CHILD].id) == db.records[DOCS].id

    site.html(DOCS, "Docs", "/docs/a.html", text="All the docs")
    await connector.run_sync()

    assert DOCS in db.records
    assert db.parent_of.get(db.records[CHILD].id) == db.records[DOCS].id


async def _remove_the_folder_page(site: FakeWeb, db: LinkingRecordsDb, make_connector: MakeConnector) -> WebConnector:
    _site(site)
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()
    site.add(DOCS, Page(status=404))
    await connector.run_sync()
    return connector


async def test_a_folder_page_whose_children_cannot_be_read_is_kept_until_they_can(
    site: FakeWeb, db: LinkingRecordsDb, make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector = await _remove_the_folder_page(site, db, make_connector)

    async def unreadable(*_: object, **__: object) -> list:
        raise RuntimeError("records database unavailable")

    readable = db.get_records_by_parent
    monkeypatch.setattr(db, "get_records_by_parent", unreadable)
    await connector.run_sync()  # the second 404 in a row, but the children can't be read
    assert DOCS in db.pages(), "removing it would drop its children out of the tree"

    monkeypatch.setattr(db, "get_records_by_parent", readable)
    await connector.run_sync()
    assert DOCS not in db.pages() and db.records[DOCS].is_internal
    assert db.parent_of.get(db.records[CHILD].id) == db.records[DOCS].id


async def test_children_a_removal_could_not_link_again_are_linked_on_the_next_sync(
    site: FakeWeb, db: LinkingRecordsDb, make_connector: MakeConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector = await _remove_the_folder_page(site, db, make_connector)
    attach = db.on_records_attached_to_parent

    async def failing(*_: object, **__: object) -> None:
        raise RuntimeError("records database unavailable")

    monkeypatch.setattr(db, "on_records_attached_to_parent", failing)
    await connector.run_sync()  # removes the folder page, but the re-link fails
    assert DOCS not in db.pages()
    assert db.records[CHILD].id not in db.parent_of

    monkeypatch.setattr(db, "on_records_attached_to_parent", attach)
    await connector.run_sync()  # the child is unchanged, so nothing else would write its link
    assert db.parent_of.get(db.records[CHILD].id) == db.records[DOCS].id


GUIDES = "http://site.test/guides/"
SETUP = "http://site.test/guides/setup/"
INSTALL = "http://site.test/guides/setup/install.html"


async def test_a_full_resync_keeps_pages_linked_under_folders_that_already_exist(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    """Neither folder is a page of the site, so only the connector writes it. One left unwritten
    on a full sync is unlinked when the sync ends, which cuts the pages under it off from the
    access they inherit through it."""
    site.html(START_URL, "Home", "/guides/setup/install.html")
    site.html(INSTALL, "Install", text="Install with pip")
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()
    db.end_full_sync()
    assert db.records[SETUP].is_internal and db.records[GUIDES].is_internal

    checkpoints.sync_points.clear()  # no sync point, so the next sync is a full one again
    await connector.run_sync()
    db.end_full_sync()

    assert db.parent_of.get(db.records[INSTALL].id) == db.records[SETUP].id
    assert db.parent_of.get(db.records[SETUP].id) == db.records[GUIDES].id


async def _damaged_by_an_earlier_full_sync(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> WebConnector:
    site.html(START_URL, "Home", "/guides/setup/install.html")
    site.html(INSTALL, "Install", text="Install with pip")
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()
    db.end_full_sync()
    db.parent_of.clear()
    checkpoints.sync_points.clear()
    return connector


async def test_a_full_sync_links_pages_again_under_folders_an_earlier_sync_unlinked(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    """The state an earlier build left: the folders have no links, nor do the pages under them.
    The page is unchanged, so only the connector can link it again."""
    connector = await _damaged_by_an_earlier_full_sync(site, db, checkpoints, make_connector)
    await connector.run_sync()
    db.end_full_sync()

    assert db.parent_of.get(db.records[INSTALL].id) == db.records[SETUP].id
    assert db.parent_of.get(db.records[SETUP].id) == db.records[GUIDES].id


async def test_pages_a_full_sync_could_not_link_again_are_linked_by_the_next_sync(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a full sync links them, so a failure must not be forgotten when that sync ends."""
    connector = await _damaged_by_an_earlier_full_sync(site, db, checkpoints, make_connector)
    attach = db.on_records_attached_to_parent

    async def failing(*_: object, **__: object) -> None:
        raise RuntimeError("records database unavailable")

    monkeypatch.setattr(db, "on_records_attached_to_parent", failing)
    await connector.run_sync()
    db.end_full_sync()
    assert db.records[INSTALL].id not in db.parent_of
    saved = next(iter(checkpoints.sync_points.values()))
    assert saved["relinkPending"] == [SETUP]

    monkeypatch.setattr(db, "on_records_attached_to_parent", attach)
    await connector.run_sync()  # incremental: the sync point is kept

    assert db.parent_of.get(db.records[INSTALL].id) == db.records[SETUP].id
    assert next(iter(checkpoints.sync_points.values()))["relinkPending"] == []


async def test_a_folder_rewritten_under_a_parent_stored_without_a_trailing_slash_keeps_its_link(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    """Older builds stored folders without the trailing slash. A reference with one names no record."""
    site.html(START_URL, "Home", "/guides/setup/install.html")
    site.html(INSTALL, "Install", text="Install with pip")
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()
    legacy = GUIDES.rstrip("/")
    guides = db.records.pop(GUIDES)
    guides.external_record_id = legacy
    db.records[legacy] = guides
    db.records[SETUP].parent_external_record_id = legacy
    db.end_full_sync()

    checkpoints.sync_points.clear()
    await connector.run_sync()
    db.end_full_sync()

    assert GUIDES not in db.records, "no second folder under the slashed id"
    assert db.records[SETUP].parent_external_record_id == legacy
    assert db.parent_of.get(db.records[SETUP].id) == guides.id


async def test_a_full_sync_links_a_page_again_under_a_parent_page_an_earlier_sync_unlinked(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    """An older build could skip the parent page (a 304, a sitemap lastmod) and unlink it with the
    links of the pages under it. Rewriting the parent page restores only its own links."""
    _site(site)
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()
    db.end_full_sync()
    del db.parent_of[db.records[CHILD].id]

    checkpoints.sync_points.clear()
    await connector.run_sync()
    db.end_full_sync()

    assert db.parent_of.get(db.records[CHILD].id) == db.records[DOCS].id


async def test_a_new_folder_names_its_parent_page_as_it_is_stored_now(
    site: FakeWeb, db: FullSyncRecordsDb, make_connector: MakeConnector
) -> None:
    """The parent page was met under an old id without its trailing slash, then the crawl stored it
    under its new id. A folder written under it later must name the new id."""
    _site(site)
    connector = await make_connector()
    await connector.run_sync()
    page = db.records.pop(DOCS)
    page.external_record_id = DOCS.rstrip("/")
    db.records[page.external_record_id] = page
    connector._ancestors_done.clear()  # a new sync meets the page first under its old id
    await connector._ensure_parent_records_exist(DOCS)
    assert DOCS in connector._ancestors_done

    db.records.pop(page.external_record_id)
    page.external_record_id = DOCS
    db.records[DOCS] = page
    await connector._ensure_parent_records_exist("http://site.test/docs/sub/")

    sub = db.records["http://site.test/docs/sub/"]
    assert sub.parent_external_record_id == DOCS
    assert db.parent_of.get(sub.id) == page.id


async def test_a_relink_left_to_the_next_sync_finds_pages_that_name_the_folder_with_a_trailing_slash(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The folder is stored without the slash, as older builds did; the page under it names it with one."""
    connector = await _damaged_by_an_earlier_full_sync(site, db, checkpoints, make_connector)
    legacy = SETUP.rstrip("/")
    folder = db.records.pop(SETUP)
    folder.external_record_id = legacy
    db.records[legacy] = folder
    assert db.records[INSTALL].parent_external_record_id == SETUP
    attach = db.on_records_attached_to_parent

    async def failing(*_: object, **__: object) -> None:
        raise RuntimeError("records database unavailable")

    monkeypatch.setattr(db, "on_records_attached_to_parent", failing)
    await connector.run_sync()
    db.end_full_sync()
    assert next(iter(checkpoints.sync_points.values()))["relinkPending"] == [legacy]

    monkeypatch.setattr(db, "on_records_attached_to_parent", attach)
    await connector.run_sync()  # incremental

    assert db.parent_of.get(db.records[INSTALL].id) == folder.id
    assert next(iter(checkpoints.sync_points.values()))["relinkPending"] == []


async def test_a_relink_left_to_the_next_sync_finds_pages_that_name_the_folder_without_a_trailing_slash(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reverse of the case above: the folder is stored with the slash, an older page names it without."""
    connector = await _damaged_by_an_earlier_full_sync(site, db, checkpoints, make_connector)
    db.records[INSTALL].parent_external_record_id = SETUP.rstrip("/")
    attach = db.on_records_attached_to_parent

    async def failing(*_: object, **__: object) -> None:
        raise RuntimeError("records database unavailable")

    monkeypatch.setattr(db, "on_records_attached_to_parent", failing)
    await connector.run_sync()
    db.end_full_sync()
    assert next(iter(checkpoints.sync_points.values()))["relinkPending"] == [SETUP]

    # The full sync rewrote the page with the slashed reference; the retry is for one that still names it without.
    db.records[INSTALL].parent_external_record_id = SETUP.rstrip("/")
    monkeypatch.setattr(db, "on_records_attached_to_parent", attach)
    await connector.run_sync()  # incremental

    assert db.parent_of.get(db.records[INSTALL].id) == db.records[SETUP].id
    assert next(iter(checkpoints.sync_points.values()))["relinkPending"] == []


async def test_a_relink_left_to_the_next_sync_finds_the_folder_rewritten_under_its_trailing_slash_since(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The relink failed under the folder's old id; by the next sync it is stored under the slashed one."""
    connector = await _damaged_by_an_earlier_full_sync(site, db, checkpoints, make_connector)
    legacy = SETUP.rstrip("/")
    folder = db.records.pop(SETUP)
    folder.external_record_id = legacy
    db.records[legacy] = folder
    attach = db.on_records_attached_to_parent

    async def failing(*_: object, **__: object) -> None:
        raise RuntimeError("records database unavailable")

    monkeypatch.setattr(db, "on_records_attached_to_parent", failing)
    await connector.run_sync()
    db.end_full_sync()
    assert next(iter(checkpoints.sync_points.values()))["relinkPending"] == [legacy]

    db.records.pop(legacy)
    folder.external_record_id = SETUP
    db.records[SETUP] = folder
    monkeypatch.setattr(db, "on_records_attached_to_parent", attach)
    await connector.run_sync()  # incremental

    assert db.parent_of.get(db.records[INSTALL].id) == folder.id
    assert next(iter(checkpoints.sync_points.values()))["relinkPending"] == []


MANUAL = "http://site.test/guides/setup/manual.pdf"


async def test_a_full_sync_fetches_a_document_the_site_would_answer_not_modified_for(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    """A 304 writes nothing, so the full sync would unlink the document and the folder above it."""
    site.html(START_URL, "Home", "/guides/setup/manual.pdf")
    site.add(MANUAL, Page(body=b"%PDF-1.4 v1", content_type="application/pdf", etag='"v1"'))
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()
    db.end_full_sync()

    checkpoints.sync_points.clear()
    await connector.run_sync()
    db.end_full_sync()

    assert site.not_modified == []
    assert db.parent_of.get(db.records[MANUAL].id) == db.records[SETUP].id
    assert db.parent_of.get(db.records[SETUP].id) == db.records[GUIDES].id


async def test_a_full_sync_fetches_a_page_the_sitemap_says_is_unchanged(
    site: FakeWeb, db: FullSyncRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    """In sitemap-only discovery an unchanged page is not fetched, so a full sync would write nothing for it."""
    site.add("http://site.test/robots.txt", Page(
        body=b"User-agent: *\nAllow: /\nSitemap: http://site.test/sm.xml\n", content_type="text/plain",
    ))
    site.add("http://site.test/sm.xml", Page(
        body=(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"<url><loc>{INSTALL}</loc><lastmod>2020-01-01</lastmod></url></urlset>"
        ).encode(),
        content_type="application/xml",
    ))
    site.html(START_URL, "Home")
    site.html(INSTALL, "Install", text="Install with pip")
    connector = await make_connector(discovery="sitemap")
    await connector.run_sync()
    db.mark_indexed()
    db.end_full_sync()

    checkpoints.sync_points.clear()
    await connector.run_sync()
    db.end_full_sync()

    run = json.loads(next(iter(checkpoints.sync_points.values()))["lastRun"])
    assert run["unchanged_by_lastmod"] == 0
    assert db.parent_of.get(db.records[INSTALL].id) == db.records[SETUP].id


async def test_a_folder_that_already_exists_is_written_once_per_sync(
    site: FakeWeb, db: LinkingRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    site.html(START_URL, "Home", "/guides/setup/install.html", "/guides/setup/upgrade.html")
    site.html(INSTALL, "Install", text="Install with pip")
    site.html("http://site.test/guides/setup/upgrade.html", "Upgrade", text="Upgrade with pip")
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()
    stored_id = db.records[SETUP].id
    db.new_batches.clear()

    checkpoints.sync_points.clear()
    await connector.run_sync()

    writes = [record for batch in db.new_batches for record in batch if record.external_record_id == SETUP]
    assert len(writes) == 1, "one write per folder per sync, not one per page under it"
    assert db.records[SETUP].id == stored_id


async def test_a_page_that_is_also_a_parent_is_not_written_over_as_a_folder(
    site: FakeWeb, db: LinkingRecordsDb, checkpoints: FakeCheckpointStore, make_connector: MakeConnector
) -> None:
    _site(site)
    connector = await make_connector()
    await connector.run_sync()
    db.mark_indexed()

    checkpoints.sync_points.clear()
    await connector.run_sync()

    assert DOCS in db.pages() and db.records[DOCS].record_name == "Docs"
