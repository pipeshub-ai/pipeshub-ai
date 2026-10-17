"""The record tree: each page hangs under the record of its URL-path parent.

The fake records database here links a record to its parent at write time and drops the
links of a deleted record, as the graph stores do (a delete removes the vertex with its
edges, and a record is linked to its parent only when it is written).
"""

import pytest
from web_behaviour_fakes import START_URL, FakeRecordsDb, FakeWeb, MakeConnector, Page

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


@pytest.fixture
def db() -> LinkingRecordsDb:
    return LinkingRecordsDb()


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
