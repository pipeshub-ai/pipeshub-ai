"""One set of cases for the insert-only record fields, run against each graph store.

``createdAtTimestamp`` is written when a record's vertex is inserted and never by an update; ``incarnationId`` is minted by the
store at insert and changed only by a restore from the trash. A placeholder replaced by a real record takes the earlier positive
date and keeps its id. The thin test modules hand each case an adapter for their store (``StoreAdapter``).
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import FileRecord, RecordType, WebpageRecord

RECORDS = CollectionNames.RECORDS.value


class StoreAdapter(Protocol):
    name: str

    async def upsert(self, records: list[FileRecord], *, release_trashed: bool = False) -> None: ...
    async def upsert_nodes(self, docs: list[dict[str, Any]]) -> None: ...
    async def doc(self, key: str) -> dict[str, Any] | None: ...
    async def type_doc(self, key: str, collection: str) -> dict[str, Any] | None: ...
    async def trash(self, key: str, connector_id: str) -> str: ...
    async def restore(self, key: str, connector_id: str, batch_id: str) -> list[str]: ...
    async def merge_like_an_older_process(self, key: str, doc: dict[str, Any]) -> None: ...


def new_key() -> str:
    return f"g55-{uuid.uuid4().hex[:12]}"


def make_record(key: str, *, created_at: int, connector_id: str = "g55-conn", placeholder: bool = False, name: str = "a.txt",
                known_new: bool = False, promotes: bool = False, ext: str | None = None) -> FileRecord:
    record = FileRecord(
        id=key, org_id="g55-org", record_name=name, record_type=RecordType.FILE, external_record_id=ext or f"ext-{key}",
        version=0, origin=OriginTypes.CONNECTOR, connector_name=Connectors.KNOWLEDGE_BASE, connector_id=connector_id,
        created_at=created_at, updated_at=created_at, is_file=True, extension="txt", is_placeholder=placeholder,
    )
    record._known_new = known_new
    record._promotes_stub = promotes
    return record


def minted(doc: dict[str, Any] | None) -> str | None:
    return None if doc is None else doc.get("incarnationId")


# ---- the cases ------------------------------------------------------------------------------------------------------

async def resync_keeps_created_at(store: StoreAdapter, flag: bool) -> None:
    """a first insert writes both fields (the id only with the flag); every later write of the record keeps them."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    first = await store.doc(key)
    assert first is not None and first["createdAtTimestamp"] == 100
    assert (minted(first) is not None) is flag
    for created in (999, 5, 1):
        await store.upsert([make_record(key, created_at=created, name=f"renamed-{created}")])
    again = await store.doc(key)
    assert again["createdAtTimestamp"] == 100
    assert minted(again) == minted(first)
    assert again["recordName"] == "renamed-1"          # the rest of the document is still updated


async def nodes_path_keeps_both_fields(store: StoreAdapter, flag: bool) -> None:
    """the dict writers' path (``batch_upsert_nodes``) is insert-only too, and a null cannot remove a stored value."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100)])
    before = await store.doc(key)
    patch = {"id": key, "orgId": "g55-org", "recordName": "from-nodes", "externalRecordId": f"ext-{key}", "recordType": "FILE",
             "origin": "CONNECTOR", "connectorName": "KB", "connectorId": "g55-conn", "createdAtTimestamp": 7,
             "incarnationId": None, "version": 0}
    await store.upsert_nodes([patch])
    after = await store.doc(key)
    assert after["createdAtTimestamp"] == 100
    assert minted(after) == minted(before)
    assert after["recordName"] == "from-nodes"


async def a_new_key_through_nodes_gets_an_id(store: StoreAdapter, flag: bool) -> None:
    """a dict writer that mints a new key gets an incarnation id at the store insert (flag on), none with it off."""
    key = new_key()
    await store.upsert_nodes([{"id": key, "orgId": "g55-org", "recordName": "dict", "externalRecordId": f"ext-{key}", "recordType": "FILE",
                               "origin": "CONNECTOR", "connectorName": "KB", "connectorId": "g55-conn", "createdAtTimestamp": 42, "version": 0}])
    doc = await store.doc(key)
    assert doc["createdAtTimestamp"] == 42
    assert (minted(doc) is not None) is flag


async def stub_then_real(store: StoreAdapter, flag: bool, *, hinted: bool) -> None:
    """the stub's date becomes the earlier positive one when the real record replaces it, its id stays, and the cycle stub,
    full, stub, full keeps the original. An unhinted promotion on ArangoDB and Neo4j keeps the stub's date (the processor marks promotions)."""
    key = new_key()
    await store.upsert([make_record(key, created_at=500, placeholder=True, known_new=True)])
    stub = await store.doc(key)
    assert stub["createdAtTimestamp"] == 500
    await store.upsert([make_record(key, created_at=300, promotes=hinted)])
    real = await store.doc(key)
    expected = 300 if (hinted or store.name == "postgres") else 500
    assert real["createdAtTimestamp"] == expected, f"{store.name}: promotion date"
    assert minted(real) == minted(stub), "a promotion never changes the id"
    assert real["isPlaceholder"] is False
    await store.upsert([make_record(key, created_at=700, placeholder=True)])          # demoted: a stub again, frozen
    assert (await store.doc(key))["createdAtTimestamp"] == expected
    await store.upsert([make_record(key, created_at=1000, promotes=hinted)])          # promoted again: min(stored, 1000)
    again = await store.doc(key)
    assert again["createdAtTimestamp"] == expected
    assert minted(again) == minted(stub)
    await store.upsert([make_record(key, created_at=1)])                              # a real record is frozen
    assert (await store.doc(key))["createdAtTimestamp"] == expected


async def a_promotion_ignores_an_unreal_date(store: StoreAdapter, flag: bool) -> None:
    """A date that is not a positive number never replaces the stored one."""
    key = new_key()
    await store.upsert([make_record(key, created_at=500, placeholder=True, known_new=True)])
    await store.upsert([make_record(key, created_at=0, promotes=True)])
    assert (await store.doc(key))["createdAtTimestamp"] == 500


async def restore_resets_the_id(store: StoreAdapter, flag: bool) -> None:
    """a restore from the trash gives a new id (flag on) and keeps the date; an upsert onto a trashed vertex neither untrashes
    it nor changes the id."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    first = await store.doc(key)
    batch = await store.trash(key, "g55-conn")
    trashed = await store.doc(key)
    assert trashed["isDeleted"] is True
    await store.upsert([make_record(key, created_at=555, name="written-while-trashed")])
    still = await store.doc(key)
    assert still["isDeleted"] is True, "an upsert must not take a record out of the trash"
    assert minted(still) == minted(first)
    restored = await store.restore(key, "g55-conn", batch)
    assert restored == [key]
    after = await store.doc(key)
    assert after["isDeleted"] is False
    assert after["createdAtTimestamp"] == 100
    if flag:
        assert minted(after) is not None and minted(after) != minted(first)
    else:
        assert minted(after) == minted(first)


async def an_id_sent_through_the_upsert_is_ignored(store: StoreAdapter, flag: bool) -> None:
    """the reset is never written by the upsert."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    first = await store.doc(key)
    forged = make_record(key, created_at=100)
    forged.incarnation_id = "forged-" + key
    await store.upsert([forged])
    assert minted(await store.doc(key)) == minted(first)


async def an_older_process_leaves_the_id(store: StoreAdapter, flag: bool) -> None:
    """a writer that does not know the field merges its document: it may overwrite the date, never the id."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    first = await store.doc(key)
    await store.merge_like_an_older_process(key, {"recordName": "old-writer", "createdAtTimestamp": 31337})
    after = await store.doc(key)
    assert minted(after) == minted(first)
    assert after["recordName"] == "old-writer"


async def concurrent_first_inserts_agree(store: StoreAdapter, flag: bool) -> None:
    """writers racing to insert one key leave one document whose fields are those one of them wrote and that stay put."""
    import asyncio

    key = new_key()
    await asyncio.gather(*(store.upsert([make_record(key, created_at=100 + n, known_new=(n % 2 == 0))]) for n in range(6)))
    doc = await store.doc(key)
    assert doc["createdAtTimestamp"] in {100 + n for n in range(6)}
    assert (minted(doc) is not None) is flag
    await store.upsert([make_record(key, created_at=9999)])
    after = await store.doc(key)
    assert after["createdAtTimestamp"] == doc["createdAtTimestamp"] and minted(after) == minted(doc)


async def a_stale_promotion_hint_is_ignored(store: StoreAdapter, flag: bool) -> None:
    """A promotion hint was read before another writer promoted the stub; the stored row is real now, so its date stays."""
    key = new_key()
    await store.upsert([make_record(key, created_at=500, known_new=True)])
    await store.upsert([make_record(key, created_at=200, promotes=True, name="stale-hint")])
    after = await store.doc(key)
    assert after["recordName"] == "stale-hint"
    assert after["createdAtTimestamp"] == 500, f"{store.name}: a real record's date moved under a stale promotion hint"


async def the_move_path_keeps_both_fields_and_releases(store: StoreAdapter, flag: bool) -> None:
    """The path that releases a trashed holder's external id is insert-only too, keeps the holder's trash batch,
    and a stub promoted through it takes the earlier date and keeps its id."""
    key, holder = new_key(), new_key()
    ext = f"ext-shared-{key}"
    await store.upsert([make_record(holder, created_at=50, ext=ext, known_new=True)])
    held = await store.doc(holder)
    batch = await store.trash(holder, "g55-conn")
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    first = await store.doc(key)
    moved = make_record(key, created_at=999, ext=ext, name="moved.txt")
    moved.incarnation_id = "forged"
    await store.upsert([moved], release_trashed=True)
    after = await store.doc(key)
    assert after["recordName"] == "moved.txt" and after["externalRecordId"] == ext
    assert after["createdAtTimestamp"] == 100, f"{store.name}: the move path is insert-only"
    assert minted(after) == minted(first)
    h = await store.doc(holder)
    assert h["isDeleted"] is True and h["externalRecordId"] != ext and h.get("trashedExternalRecordId") == ext, h
    assert h["createdAtTimestamp"] == 50 and minted(h) == minted(held)
    assert h.get("deleteBatchId") == batch, "the release keeps the trash batch"
    stub_key = new_key()
    await store.upsert([make_record(stub_key, created_at=500, placeholder=True, known_new=True)])
    stub = await store.doc(stub_key)
    await store.upsert([make_record(stub_key, created_at=300, promotes=True)], release_trashed=True)
    promoted = await store.doc(stub_key)
    assert promoted["createdAtTimestamp"] == 300 and promoted["isPlaceholder"] is False
    assert minted(promoted) == minted(stub)


async def with_the_kill_switch_off_no_store_mints(store: StoreAdapter, flag: bool) -> None:
    """``RECORD_CREATED_AT_INSERT_ONLY=false`` restores the older write on every store, so none mints an id (a minted id would be
    overwritten by the next update). Run with the switch off and the incarnation flag on."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    assert minted(await store.doc(key)) is None, f"{store.name} minted an id with the kill switch off"
    await store.upsert([make_record(key, created_at=999)])
    assert (await store.doc(key))["createdAtTimestamp"] == 999, "the older write overwrites the date"


async def a_null_removes_the_stored_key(store: StoreAdapter, flag: bool) -> None:
    """An update carrying None removes the key on every store, as the older write did (ArangoDB stored the null)."""
    key = new_key()
    first = make_record(key, created_at=100, known_new=True)
    first.weburl = "https://example.com/a"
    await store.upsert([first])
    assert (await store.doc(key))["webUrl"] == "https://example.com/a"
    second = make_record(key, created_at=200)
    second.weburl = None
    await store.upsert([second])
    assert "webUrl" not in (await store.doc(key)), f"{store.name} kept the null"


async def a_marked_stub_is_no_promotion(store: StoreAdapter, flag: bool) -> None:
    """A row marked as a promotion that is itself a placeholder takes no date from the stored stub on any store."""
    key = new_key()
    await store.upsert([make_record(key, created_at=500, placeholder=True, known_new=True)])
    await store.upsert([make_record(key, created_at=300, placeholder=True, promotes=True)])
    assert (await store.doc(key))["createdAtTimestamp"] == 500, f"{store.name}: a stub took an earlier date"


async def with_the_kill_switch_off_a_promotion_takes_the_incoming_date(store: StoreAdapter, flag: bool) -> None:
    """With ``RECORD_CREATED_AT_INSERT_ONLY=false`` the older merge-everything write holds on every path, a stub promotion
    (ordinary and trash-release) included: the incoming date replaces the stored one."""
    for release in (False, True):
        key = new_key()
        await store.upsert([make_record(key, created_at=500, placeholder=True, known_new=True)])
        await store.upsert([make_record(key, created_at=999, promotes=True)], release_trashed=release)
        assert (await store.doc(key))["createdAtTimestamp"] == 999, f"{store.name} (release={release}) kept the stored date"


async def a_partial_node_patch_is_no_promotion(store: StoreAdapter, flag: bool) -> None:
    """A dict patch of a stored placeholder that names an earlier date but does not say ``isPlaceholder: false`` is no promotion."""
    key = new_key()
    await store.upsert([make_record(key, created_at=500, placeholder=True, known_new=True)])
    await store.upsert_nodes([{"id": key, "orgId": "g55-org", "recordName": "patch", "externalRecordId": f"ext-{key}", "recordType": "FILE",
                               "origin": "CONNECTOR", "connectorName": "KB", "connectorId": "g55-conn", "createdAtTimestamp": 300,
                               "version": 0}])
    doc = await store.doc(key)
    assert doc["createdAtTimestamp"] == 500 and doc["isPlaceholder"] is True, f"{store.name}: {doc}"


async def repeated_ids_keep_the_last_write(store: StoreAdapter, flag: bool) -> None:
    """One batch writing a record three times (the middle row marked as a promotion) ends with the last row's fields."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    await store.upsert([
        make_record(key, created_at=100, name="first"),
        make_record(key, created_at=100, name="second", promotes=True),
        make_record(key, created_at=100, name="third"),
    ])
    assert (await store.doc(key))["recordName"] == "third", store.name


async def complementary_documents_for_one_new_id_jointly_make_the_insert(store: StoreAdapter, flag: bool) -> None:
    """Two dict documents for one new id, the date only in the second, store that date (the first creates the vertex)."""
    key = new_key()
    base = {"id": key, "orgId": "g55-org", "recordName": "dup", "externalRecordId": f"ext-{key}", "recordType": "FILE",
            "origin": "CONNECTOR", "connectorName": "KB", "connectorId": "g55-conn", "version": 0}
    await store.upsert_nodes([base, {"id": key, "orgId": "g55-org", "createdAtTimestamp": 42, "recordName": "dup2"}])
    doc = await store.doc(key)
    assert doc["createdAtTimestamp"] == 42 and doc["recordName"] == "dup2", f"{store.name}: {doc}"


async def a_restore_survives_a_same_id_write_with_the_kill_switch_off(store: StoreAdapter, flag: bool) -> None:
    """With the insert-only switch off (and the incarnation flag on), the write that follows a restore keeps the restored id."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    batch = await store.trash(key, "g55-conn")
    assert await store.restore(key, "g55-conn", batch) == [key]
    restored = minted(await store.doc(key))
    assert restored is not None
    await store.upsert([make_record(key, created_at=200, name="after-restore")])
    assert minted(await store.doc(key)) == restored, store.name
    base = {"id": key, "orgId": "g55-org", "recordName": "dict", "externalRecordId": f"ext-{key}", "recordType": "FILE",
            "origin": "CONNECTOR", "connectorName": "KB", "connectorId": "g55-conn", "version": 0}
    for sent in ("forged", None):          # a dict that names another id, or a null, leaves the stored id alone
        await store.upsert_nodes([{**base, "incarnationId": sent}])
        assert minted(await store.doc(key)) == restored, f"{store.name}: {sent!r} changed the id"


async def a_carried_id_never_starts_a_new_vertex(store: StoreAdapter, flag: bool) -> None:
    """A record that holds an incarnation id (copied from a stored one) inserted as a new vertex gets one minted by the store."""
    key = new_key()
    stale = make_record(key, created_at=100, known_new=True)
    stale.incarnation_id = "stale-" + key
    await store.upsert([stale])
    stored = minted(await store.doc(key))
    assert stored != "stale-" + key
    assert (stored is not None) is flag


async def one_id_twice_in_a_batch_is_merged_by_one_rule(store: StoreAdapter, flag: bool) -> None:
    """Two rows for one new id in one call: ``createdAtTimestamp`` is the earliest positive value whichever row carries it, and the later
    row wins for every other key; keys only the earlier row sets stay. The same result on every store."""
    for first_date, second_date in ((100, 200), (200, 100)):
        key = new_key()
        base = {"id": key, "orgId": "g55-org", "externalRecordId": f"ext-{key}", "recordType": "FILE", "origin": "CONNECTOR",
                "connectorName": "KB", "connectorId": "g55-conn", "version": 0}
        await store.upsert_nodes([
            {**base, "recordName": "first", "createdAtTimestamp": first_date, "sizeInBytes": 5},
            {**base, "recordName": "second", "createdAtTimestamp": second_date},
        ])
        doc = await store.doc(key)
        assert doc["createdAtTimestamp"] == 100, f"{store.name}: {doc['createdAtTimestamp']} for dates {first_date}, {second_date}"
        assert doc["recordName"] == "second" and doc["sizeInBytes"] == 5, f"{store.name}: {doc}"


async def batch_upsert_records_with_one_id_twice_is_one_record(store: StoreAdapter, flag: bool) -> None:
    """Two records for one new id in one ``batch_upsert_records`` call, in both date orders: ``createdAtTimestamp`` is the earliest positive
    value, the later record's fields win, and the type document and edge are written once. The same on every store."""
    for first_date, second_date in ((100, 200), (200, 100)):
        key = new_key()
        await store.upsert([
            make_record(key, created_at=first_date, name="first", known_new=True),
            make_record(key, created_at=second_date, name="second", known_new=True),
        ])
        doc = await store.doc(key)
        assert doc["createdAtTimestamp"] == 100, f"{store.name}: {doc['createdAtTimestamp']} for dates {first_date}, {second_date}"
        assert doc["recordName"] == "second", store.name
        assert (await store.type_doc(key, CollectionNames.FILES.value)) is not None


async def batch_upsert_records_with_one_id_twice_keeps_the_last_type(store: StoreAdapter, flag: bool) -> None:
    """The same id as a file and then as a web page: the last row's type wins, so only its type document is written."""
    key = new_key()
    page = WebpageRecord(
        id=key, org_id="g55-org", record_name="page", record_type=RecordType.WEBPAGE, external_record_id=f"ext-{key}", version=0,
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.KNOWLEDGE_BASE, connector_id="g55-conn", created_at=50, updated_at=50,
    )
    page._known_new = True
    await store.upsert([make_record(key, created_at=100, known_new=True), page])
    doc = await store.doc(key)
    assert doc["recordType"] == "WEBPAGE" and doc["createdAtTimestamp"] == 50, f"{store.name}: {doc}"
    assert await store.type_doc(key, CollectionNames.WEBPAGES.value) is not None
    assert await store.type_doc(key, CollectionNames.FILES.value) is None, f"{store.name} wrote the first row's type document"


async def a_carried_id_on_a_dict_insert_is_replaced(store: StoreAdapter, flag: bool) -> None:
    """A dict insert through ``batch_upsert_nodes`` that carries an incarnation id (copied from a deleted record) gets a freshly minted
    one, or none when minting is off."""
    key = new_key()
    await store.upsert_nodes([{"id": key, "orgId": "g55-org", "recordName": "dict", "externalRecordId": f"ext-{key}", "recordType": "FILE",
                               "origin": "CONNECTOR", "connectorName": "KB", "connectorId": "g55-conn", "version": 0,
                               "createdAtTimestamp": 9, "incarnationId": "copied-" + key}])
    stored = minted(await store.doc(key))
    assert stored != "copied-" + key, store.name
    assert (stored is not None) is flag, store.name


async def with_the_kill_switch_off_repeated_ids_are_written_in_order(store: StoreAdapter, flag: bool) -> None:
    """With ``RECORD_CREATED_AT_INSERT_ONLY=false`` a batch that repeats an id behaves as it did before the switch existed: the last row
    wins, dates included, for typed records and for dict writers alike. Run with the switch off."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, name="first", known_new=True), make_record(key, created_at=5000, name="second", known_new=True)])
    doc = await store.doc(key)
    assert doc["createdAtTimestamp"] == 5000 and doc["recordName"] == "second", f"{store.name} typed: {doc}"
    other = new_key()
    base = {"id": other, "orgId": "g55-org", "externalRecordId": f"ext-{other}", "recordType": "FILE", "origin": "CONNECTOR",
            "connectorName": "KB", "connectorId": "g55-conn", "version": 0}
    await store.upsert_nodes([{**base, "recordName": "first", "createdAtTimestamp": 100}, {**base, "recordName": "second", "createdAtTimestamp": 5000}])
    doc = await store.doc(other)
    assert doc["createdAtTimestamp"] == 5000 and doc["recordName"] == "second", f"{store.name} dict: {doc}"


async def type_documents_keep_the_merge(store: StoreAdapter, flag: bool) -> None:
    """only the ``records`` document is insert-only; the type document (``files``) is written as before."""
    key = new_key()
    await store.upsert([make_record(key, created_at=100, known_new=True)])
    await store.upsert([make_record(key, created_at=999, name="second.txt")])
    assert (await store.doc(key))["createdAtTimestamp"] == 100
    type_doc = await store.type_doc(key, CollectionNames.FILES.value)
    assert type_doc is not None and type_doc["name"] == "second.txt", "the type document is merged as before"


ALL_CASES = (
    a_carried_id_on_a_dict_insert_is_replaced,
    batch_upsert_records_with_one_id_twice_is_one_record, batch_upsert_records_with_one_id_twice_keeps_the_last_type,
    one_id_twice_in_a_batch_is_merged_by_one_rule,
    a_carried_id_never_starts_a_new_vertex,
    complementary_documents_for_one_new_id_jointly_make_the_insert,
    a_partial_node_patch_is_no_promotion, repeated_ids_keep_the_last_write,
    a_null_removes_the_stored_key, a_marked_stub_is_no_promotion,
    a_stale_promotion_hint_is_ignored, the_move_path_keeps_both_fields_and_releases,
    type_documents_keep_the_merge,
    resync_keeps_created_at, nodes_path_keeps_both_fields, a_new_key_through_nodes_gets_an_id, a_promotion_ignores_an_unreal_date,
    restore_resets_the_id, an_id_sent_through_the_upsert_is_ignored, an_older_process_leaves_the_id, concurrent_first_inserts_agree,
)
