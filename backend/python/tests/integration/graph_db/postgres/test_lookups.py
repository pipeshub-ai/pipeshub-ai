"""Lookups, listings, the parent adjacency and the group moves of records on PostgreSQL, through the provider's own API.

Each test seeds through the provider and asserts what a caller sees (the scenarios of the Neo4j-parity suite of the records part, run on one PostgreSQL org).
Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.core import org_context
from app.core.db.uow import Tx
from app.core.org_context import MissingOrgContext, SystemContextNotAllowed
from app.models.entities import (
    FileRecord,
    MailRecord,
    MessageRecord,
    Person,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.services.graph_db.postgres.lookups import LINKING_PROFILE, LookupsMixin
from tests.support import plan_gate

from .pg_support import OrgBound, StandInProvider, new_org
from .test_code_hop_paging import _admin

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration
RECORDS = CollectionNames.RECORDS.value


@dataclass
class G:
    graph: OrgBound
    backend: str
    org: str
    conn: str
    ids: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)

    async def rec(self, name: str, *, parent: FileRecord | None = None, folder: bool = False, group: RecordGroup | None = None,
                  attach_to: FileRecord | None = None, **fields: object) -> FileRecord:
        owner = parent or attach_to
        r = FileRecord(
            id=str(uuid.uuid4()), org_id=self.org, record_name=name, external_record_id=f"ext-{name}-{uuid.uuid4().hex[:6]}", version=1,
            origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=self.conn,
            mime_type=MimeTypes.FOLDER.value if folder else MimeTypes.PDF.value, record_type=RecordType.FILE, is_file=not folder,
            parent_external_record_id=owner.external_record_id if owner else None,
            external_record_group_id=group.external_group_id if group else None, record_group_id=group.id if group else None,
            **fields,
        )
        await self.graph.batch_upsert_records([r])
        # Indexing stamps the virtual record id, not the sync that writes the record.
        r.virtual_record_id = f"vr-{name}-{uuid.uuid4().hex[:6]}"
        await self.graph.update_node(r.id, RECORDS, {"virtualRecordId": r.virtual_record_id})
        self.ids.append(r.id)
        if owner:
            await self.graph.create_record_relation(owner.id, r.id, "ATTACHMENT" if attach_to else "PARENT_CHILD")
        if group:
            await self.graph.create_record_group_relation(r.id, group.id)
        return r

    async def group(self, name: str, parent: RecordGroup | None = None) -> RecordGroup:
        g = RecordGroup(org_id=self.org, name=name, external_group_id=f"g-{name}-{uuid.uuid4().hex[:6]}", connector_name=Connectors.GOOGLE_DRIVE,
                        connector_id=self.conn, group_type=RecordGroupType.DRIVE)
        await self.graph.batch_upsert_record_groups([g])
        self.groups.append(g.id)
        if parent:
            await self.graph.create_record_groups_relation(g.id, parent.id)
        return g

    async def live(self, *rs: FileRecord) -> list[bool]:
        got = await self.graph.get_existing_record_keys([r.id for r in rs])
        return [r.id in got for r in rs]


@pytest.fixture
async def g(pg_db) -> AsyncIterator[G]:
    org = new_org()
    graph = OrgBound(StandInProvider(logging.getLogger("lookups"), pg_db), org)
    yield G(graph, "postgres", org, f"conn-{uuid.uuid4().hex[:8]}")


async def test_lookups_by_id_status_type_and_key(g: G) -> None:
    a = await g.rec("a")
    b = await g.rec("b", indexing_status="COMPLETED")
    c = await g.rec("c", indexing_status="COMPLETED", is_placeholder=True)

    assert await g.graph.get_existing_record_keys([a.id, b.id, "nope", a.id]) == {a.id, b.id}
    typed = await g.graph.get_typed_records_batch([a.id, b.id, "nope"])
    assert set(typed) == {a.id, b.id} and isinstance(typed[a.id], FileRecord)
    assert {r.id for r in await g.graph.get_records_by_record_type(g.conn, "FILE")} == {a.id, b.id, c.id}
    assert (await g.graph.get_file_record_by_id(a.id)).record_name == "a"
    assert await g.graph.get_file_record_by_id("nope") is None
    assert {x["id"] if "id" in x else x["_key"] for x in await g.graph.get_records_by_record_ids([a.id, b.id, "nope"], g.org)} == {a.id, b.id}
    assert await g.graph.get_records_by_record_ids([a.id], "another-org") == []
    assert await g.graph.get_virtual_record_ids_for_record_ids([a.id, "nope"]) == {a.id: a.virtual_record_id}
    assert await g.graph.get_virtual_record_ids_in_use([a.virtual_record_id, "vr-none"]) == {a.virtual_record_id}
    assert (await g.graph.get_record_by_external_revision_id(g.conn, "rev-none")) is None

    done = await g.graph.get_records_by_status(g.org, g.conn, ["COMPLETED"])
    assert {r.id for r in done} == {b.id, c.id}
    assert {r.id for r in await g.graph.get_records_by_status(g.org, g.conn, ["COMPLETED"], is_placeholder=False)} == {b.id}
    assert {r.id for r in await g.graph.get_records_by_status(g.org, g.conn, None, exclude_statuses=["COMPLETED"])} == {a.id}
    ordered = sorted([a.id, b.id, c.id])
    page1 = await g.graph.get_records_by_status(g.org, g.conn, None, limit=2)
    page2 = await g.graph.get_records_by_status(g.org, g.conn, None, after_key=page1[-1].id, limit=2)
    assert [r.id for r in page1] == ordered[:2] and [r.id for r in page2] == ordered[2:]
    assert [r.id for r in await g.graph.get_records_by_status(g.org, g.conn, None, limit=1, offset=1)] == ordered[1:2]
    with pytest.raises(ValueError, match="record status field"):
        await g.graph.get_records_by_status(g.org, g.conn, None, status_field="recordName")

    await g.graph.update_indexing_status_for_record_ids([a.id, c.id, "nope"], "FAILED")
    assert (await g.graph.get_document(a.id, RECORDS))["indexingStatus"] == "FAILED"
    assert (await g.graph.get_document(c.id, RECORDS))["indexingStatus"] == "COMPLETED"  # a placeholder is skipped


async def test_a_field_set_to_none_is_cleared_by_the_next_upsert(g: G) -> None:
    folder = await g.rec("folder", folder=True)
    child = await g.rec("child", parent=folder)
    assert (await g.graph.get_record_by_id(child.id)).parent_external_record_id == folder.external_record_id
    child.parent_external_record_id = None
    await g.graph.batch_upsert_records([child])
    assert (await g.graph.get_record_by_id(child.id)).parent_external_record_id is None


async def test_weburl_issue_key_duplicates_and_paths(g: G) -> None:
    url = f"https://example.test/{g.conn}/doc"
    a = await g.rec("a", weburl=url, md5_hash="m1", size_in_bytes=5)
    b = await g.rec("b", weburl=url + "2", md5_hash="m1", size_in_bytes=5)
    await g.rec("gone", md5_hash="m1", size_in_bytes=5, is_deleted=True)

    assert (await g.graph.get_record_by_weburl(url, g.org)).id == a.id
    assert await g.graph.get_record_by_weburl(url, "another-org") is None
    assert {r["id"] for r in await g.graph.get_records_by_weburls([url, url + "2"], g.org)} == {a.id, b.id}
    dups = await g.graph.find_duplicate_records(a.id, "m1", g.org, size_in_bytes=5)
    assert {d.get("id") or d.get("_key") for d in dups} == {b.id}
    assert await g.graph.find_duplicate_records(a.id, "m1", g.org, size_in_bytes=6) == []
    assert await g.graph.get_record_by_issue_key(g.conn, "ABC-1") is None


async def test_edges_helpers(g: G) -> None:
    a, b, c = await g.rec("a"), await g.rec("b"), await g.rec("c")
    edge = {"from_id": a.id, "from_collection": RECORDS, "to_id": b.id, "to_collection": RECORDS, "relationshipType": "RELATED", "keep": 1}
    await g.graph.create_edges_if_absent([edge], CollectionNames.NODE_RELATIONS.value)
    await g.graph.create_edges_if_absent([{**edge, "keep": 2}], CollectionNames.NODE_RELATIONS.value)  # an existing edge keeps its properties
    await g.graph.create_edges_if_absent([{**edge, "to_id": "nope"}], CollectionNames.NODE_RELATIONS.value)  # an end that is missing writes nothing
    out = await g.graph.get_edges_from_node(f"records/{a.id}", CollectionNames.NODE_RELATIONS.value)
    assert [(e["_to"], e.get("keep")) for e in out] == [(f"records/{b.id}", 1)]
    named = await g.graph.get_edges_from_node_with_target_name(f"records/{a.id}", CollectionNames.NODE_RELATIONS.value)
    assert [(e["_to"], e["name"]) for e in named] == [(f"records/{b.id}", "b")]
    assert [x["record_id"] for x in await g.graph.get_parent_record_ids_by_relation_type(a.id, "RELATED")] == [b.id]
    assert [x["record_id"] for x in await g.graph.get_child_record_ids_by_relation_type(b.id, "RELATED")] == [a.id]
    assert await g.graph.get_related_node_field(f"records/{a.id}", CollectionNames.NODE_RELATIONS.value, RECORDS, "recordName", "outbound") == ["b"]

    assert await g.graph.delete_edges_by_relationship_types(a.id, RECORDS, CollectionNames.NODE_RELATIONS.value, ["RELATED"]) == 1
    await g.graph.create_record_relation(a.id, c.id, "PARENT_CHILD")
    await g.graph.create_record_relation(a.id, b.id, "LINKED_TO")
    # nodeRelations is the hierarchy: a link between the records stays
    assert await g.graph.delete_all_edges_for_node(f"records/{a.id}", CollectionNames.NODE_RELATIONS.value) == 1
    assert [e["relationshipType"] for e in await g.graph.get_edges_from_node(f"records/{a.id}", CollectionNames.NODE_RELATIONS.value)] == ["LINKED_TO"]
    assert await g.graph.delete_all_edges_for_node(f"records/{a.id}", CollectionNames.NODE_RELATIONS.value) == 0
    assert await g.graph.delete_all_edges_for_node(f"records/{a.id}", CollectionNames.RECORD_LINKS.value) == 1

    await g.graph.create_record_relation(a.id, c.id, "PARENT_CHILD")
    assert await g.graph.delete_parent_child_edge_to_record(c.id) is True
    assert await g.graph.delete_parent_child_edge_to_record(c.id) is False
    await g.graph.create_record_relation(a.id, c.id, "ATTACHMENT")
    assert await g.graph.delete_edges_between_collections(a.id, RECORDS, CollectionNames.NODE_RELATIONS.value, RECORDS) == 1


async def test_update_node_if_match_replaces_only_while_the_field_matches(g: G) -> None:
    if g.backend == "neo4j":
        pytest.skip("Neo4j validates the node against the collection schema first")
    a = await g.rec("a", size_in_bytes=3)
    doc = await g.graph.get_document(a.id, RECORDS)
    assert not await g.graph.update_node_if_match(a.id, RECORDS, {**doc, "recordName": "x"}, "sizeInBytes", 9)
    assert await g.graph.update_node_if_match(a.id, RECORDS, {**doc, "recordName": "x"}, "sizeInBytes", 3)
    assert (await g.graph.get_document(a.id, RECORDS))["recordName"] == "x"


# ---- hierarchy ---------------------------------------------------------------------------------------------------


async def _tree(g: G) -> dict[str, FileRecord]:
    root = await g.rec("root", folder=True)
    sub = await g.rec("sub", folder=True, parent=root)
    f1 = await g.rec("f1", parent=root)
    f2 = await g.rec("f2", parent=sub)
    f3 = await g.rec("f3", parent=sub, indexing_status="COMPLETED")
    att = await g.rec("att", attach_to=f2)
    return {"root": root, "sub": sub, "f1": f1, "f2": f2, "f3": f3, "att": att}


async def test_parents_paths_and_depths(g: G) -> None:
    t = await _tree(g)
    assert await g.graph.get_record_path(t["f2"].id) == "root/sub/f2"
    assert await g.graph.get_record_path_segments(t["att"].id) == ["root", "sub", "f2", "att"]
    assert await g.graph.get_record_path("nope") is None
    assert await g.graph.get_record_parent_info(t["f1"].id) == {"id": t["root"].id, "type": "record"}
    assert await g.graph.get_record_parent_info(t["root"].id) is None
    assert set(await g.graph.get_descendant_virtual_record_ids(t["root"].id)) == {t[k].virtual_record_id for k in ("sub", "f1", "f2", "f3", "att")}
    depths = await g.graph.get_node_depths_batch(t["root"].id, [t["sub"].id, t["f2"].id, t["att"].id, "nope"], max_depth=3)
    assert depths == {t["sub"].id: 1, t["f2"].id: 2, t["att"].id: 3}
    adj = await g.graph.get_record_parent_adjacency([t["f2"].id], g.org)
    assert set(adj["nodes"]) == {t["f2"].id, t["sub"].id, t["root"].id}
    assert [p["parent_id"] for p in adj["parents"][t["f2"].id]] == [t["sub"].id]


async def test_children_by_parent_record_with_depth_and_status(g: G) -> None:
    t = await _tree(g)

    def ids(rs: list) -> set[str]:
        return {r.id for r in rs}
    assert ids(await g.graph.get_records_by_parent_record(t["root"].id, g.conn, g.org, 0)) == {t["root"].id}
    assert ids(await g.graph.get_records_by_parent_record(t["root"].id, g.conn, g.org, 1)) == {t["root"].id, t["sub"].id, t["f1"].id}
    assert ids(await g.graph.get_records_by_parent_record(t["root"].id, g.conn, g.org, -1)) == {t[k].id for k in t}
    assert ids(await g.graph.get_records_by_parent_record(t["root"].id, g.conn, g.org, -1, status_filters=["COMPLETED"])) == {t["f3"].id}
    ordered = sorted(t[k].id for k in t)
    page = await g.graph.get_records_by_parent_record(t["root"].id, g.conn, g.org, -1, limit=2, offset=2)
    assert [r.id for r in page] == ordered[2:4]
    after = await g.graph.get_records_by_parent_record(t["root"].id, g.conn, g.org, -1, after_key=ordered[3])
    assert [r.id for r in after] == ordered[4:]
    assert await g.graph.get_records_by_parent_record(t["root"].id, g.conn, "another-org", -1) == [] or g.backend == "neo4j"


async def test_records_of_a_group_and_its_nested_groups(g: G) -> None:
    top, inner = await g.group("top"), None
    inner = await g.group("inner", parent=top)
    a = await g.rec("a", group=top)
    b = await g.rec("b", group=inner)
    folder = await g.rec("folder", folder=True, group=top)  # a folder is not listed (its type document says isFile = false)

    got = lambda rs: {r.id for r in rs}  # noqa: E731
    assert got(await g.graph.get_records_by_record_group(top.id, g.conn, g.org, 0)) == {a.id}
    assert got(await g.graph.get_records_by_record_group(top.id, g.conn, g.org, 1)) == {a.id, b.id}
    assert got(await g.graph.get_records_by_record_group(top.id, g.conn, g.org, -1)) == {a.id, b.id}
    assert folder.id not in got(await g.graph.get_records_by_record_group(top.id, g.conn, g.org, -1))
    assert await g.graph.get_records_by_record_group(top.id, g.conn, "another-org", -1) == []
    assert (await g.graph.get_record_group_by_id(inner.id))["groupName"] == "inner"
    assert await g.graph.get_record_group_path(inner.id) == ["top", "inner"]


async def test_link_record_to_group_moves_a_record_between_groups(g: G) -> None:
    one, two = await g.group("one"), await g.group("two")
    r = await g.rec("r", group=one)

    removed = await g.graph.link_record_to_group(r.id, two.id, inherit=True, leaving_group_id=one.id, browse_root=True)
    assert removed == 0
    got = await g.graph.get_edges_from_node(f"records/{r.id}", CollectionNames.BELONGS_TO.value)
    assert [e["_to"] for e in got] == [f"recordGroups/{two.id}"]
    assert len(await g.graph.get_edges_from_node(f"records/{r.id}", CollectionNames.INHERIT_PERMISSIONS.value)) == 1
    assert await g.graph.link_record_to_group(r.id, two.id, inherit=False, browse_root=False) == 1


# ---- delete ------------------------------------------------------------------------------------------------------


async def test_a_record_by_its_folder_path_and_by_name_in_a_parent(g: G) -> None:
    app = CollectionNames.APPS.value
    await g.graph.batch_upsert_nodes([{"id": g.conn, "name": "Drive", "type": "Drive", "orgId": g.org, "isActive": True}], app)
    group = await g.group("share")
    await g.graph.batch_create_edges([{"from_id": group.id, "from_collection": CollectionNames.RECORD_GROUPS.value, "to_id": g.conn, "to_collection": app}],
                                     CollectionNames.BELONGS_TO.value)
    root = await g.rec("top", folder=True, group=group)
    sub = await g.rec("sub", folder=True, parent=root, group=group)
    leaf = await g.rec("leaf.pdf", parent=sub, group=group)
    for r in (root,):
        await g.graph.update_node(r.id, RECORDS, {"externalParentId": None})

    found = await g.graph.get_record_by_path(g.conn, ["top", "sub", "leaf.pdf"], group.external_group_id)
    assert (found.get("id") or found.get("_key")) == leaf.id
    top = await g.graph.get_record_by_path(g.conn, ["top"], group.external_group_id)
    assert (top.get("id") or top.get("_key")) == root.id
    assert await g.graph.get_record_by_path(g.conn, ["top", "nope"], group.external_group_id) is None

    again = await g.graph.find_file_by_name_in_parent("kb", "LEAF.pdf", MimeTypes.PDF.value, sub.id)
    assert again and again["_key"] == leaf.id and again["name"] == "leaf.pdf"
    assert await g.graph.find_file_by_name_in_parent("kb", "LEAF.pdf", MimeTypes.PDF.value, sub.id, exclude_record_id=leaf.id) is None
    assert await g.graph.find_file_by_name_in_parent("kb", "LEAF.pdf", MimeTypes.HTML.value, sub.id) is None


async def test_mail_message_file_and_duplicate_lookups(g: G) -> None:
    mail = MailRecord(id=str(uuid.uuid4()), org_id=g.org, record_name="m", external_record_id="ext-m", version=1, origin=OriginTypes.CONNECTOR,
                      connector_name=Connectors.GOOGLE_MAIL, connector_id=g.conn, record_type=RecordType.MAIL, thread_id="t1", conversation_index="ci1")
    msg = MessageRecord(id=str(uuid.uuid4()), org_id=g.org, record_name="s", external_record_id="ext-s", version=1, origin=OriginTypes.CONNECTOR,
                        connector_name=Connectors.SLACK_WORKSPACE, connector_id=g.conn, record_type=RecordType.MESSAGE, external_record_group_id="chan",
                        start_ts="100.1", end_ts="200.1")
    await g.graph.batch_upsert_records([mail, msg])
    g.ids += [mail.id, msg.id]
    f = await g.rec("with-path", path="/a/b.pdf", md5_hash="dup-md5")

    got = await g.graph.find_slack_burst_record_by_ts(g.conn, "chan", "150.0")
    assert got is not None and got.id == msg.id
    assert await g.graph.find_slack_burst_record_by_ts(g.conn, "chan", "300.0") is None
    assert [r.id for r in await g.graph.get_records_by_file_path(g.conn, "/a/b.pdf")] == [f.id]

    user_key, user_id = f"u-{uuid.uuid4().hex[:6]}", f"uid-{uuid.uuid4().hex[:6]}"
    await g.graph.batch_upsert_nodes([{"id": user_key, "userId": user_id, "orgId": g.org, "email": "u@example.com"}], CollectionNames.USERS.value)
    await g.graph.batch_create_edges([{"from_id": user_key, "from_collection": CollectionNames.USERS.value, "to_id": mail.id, "to_collection": RECORDS,
                                       "type": "USER", "role": "OWNER"}], CollectionNames.PERMISSION.value)
    owned = await g.graph.get_record_by_conversation_index(g.conn, "ci1", "t1", g.org, user_id)
    assert owned is not None and owned.id == mail.id
    assert await g.graph.get_record_by_conversation_index(g.conn, "ci1", "t1", g.org, "someone-else") is None

    # a mail owned by a source account the user is linked to in this connector is the user's, and nobody else's
    other = MailRecord(id=str(uuid.uuid4()), org_id=g.org, record_name="m2", external_record_id="ext-m2", version=1, origin=OriginTypes.CONNECTOR,
                       connector_name=Connectors.GOOGLE_MAIL, connector_id=g.conn, record_type=RecordType.MAIL, thread_id="t2", conversation_index="ci2")
    await g.graph.batch_upsert_records([other])
    g.ids.append(other.id)
    account = f"acct-{uuid.uuid4().hex[:6]}"
    await g.graph.batch_upsert_nodes([{"id": account, "orgId": g.org, "connectorId": g.conn, "sourceUserId": "s1", "state": "listed",
                                       "createdAtTimestamp": 1, "updatedAtTimestamp": 1}], CollectionNames.SOURCE_ACCOUNTS.value)
    await g.graph.batch_create_edges([{"from_id": account, "from_collection": CollectionNames.SOURCE_ACCOUNTS.value, "to_id": other.id,
                                       "to_collection": RECORDS, "type": "USER", "role": "OWNER"}], CollectionNames.PERMISSION.value)
    assert await g.graph.get_record_by_conversation_index(g.conn, "ci2", "t2", g.org, user_id) is None
    await g.graph.batch_create_edges([{"from_id": user_key, "from_collection": CollectionNames.USERS.value, "to_id": account,
                                       "to_collection": CollectionNames.SOURCE_ACCOUNTS.value, "connectorId": g.conn, "via": "directory",
                                       "createdAtTimestamp": 1}], CollectionNames.AUTHENTICATED_AS.value)
    owned = await g.graph.get_record_by_conversation_index(g.conn, "ci2", "t2", g.org, user_id)
    assert owned is not None and owned.id == other.id
    assert await g.graph.update_node(account, CollectionNames.SOURCE_ACCOUNTS.value, {"state": "withdrawn"})
    assert await g.graph.get_record_by_conversation_index(g.conn, "ci2", "t2", g.org, user_id) is None, "a withdrawn account links nobody"

    await g.graph.update_node(f.id, RECORDS, {"duplicateReconcilePending": True, "duplicateReconcileDueAt": 5, "duplicateReconcileAttempts": 2})
    rows = await g.graph.get_records_pending_duplicate_reconcile(due_before_ms=10, limit=100)
    assert {"_key": f.id, "orgId": g.org, "duplicateReconcileAttempts": 2, "duplicateReconcileDueAt": 5} in rows
    assert not [r for r in await g.graph.get_records_pending_duplicate_reconcile(due_before_ms=5, limit=100) if r["_key"] == f.id]


async def test_taxonomy_edges_are_copied_to_another_record_and_people_are_upserted(g: G) -> None:
    src, dst = await g.rec("src"), await g.rec("dst")
    topic = f"topic-{uuid.uuid4().hex[:6]}"
    await g.graph.batch_upsert_nodes([{"id": topic, "name": "t", "normalizedName": "t", "orgId": g.org, "createdAtTimestamp": 1}], CollectionNames.TOPICS.value)
    await g.graph.batch_create_edges([{"from_id": src.id, "from_collection": RECORDS, "to_id": topic, "to_collection": CollectionNames.TOPICS.value,
                                       "createdAtTimestamp": 7}], CollectionNames.BELONGS_TO_TOPIC.value)

    assert await g.graph.copy_document_relationships(src.id, dst.id) is True
    assert await g.graph.copy_document_relationships(src.id, dst.id) is True  # an edge the target has stays
    edges = await g.graph.get_edges_from_node(f"records/{dst.id}", CollectionNames.BELONGS_TO_TOPIC.value)
    assert [(e["_to"], e.get("createdAtTimestamp")) for e in edges] == [(f"topics/{topic}", 7)]

    person = Person(email=f"{uuid.uuid4().hex[:6]}@example.com", org_id=g.org)
    await g.graph.batch_upsert_people([person])
    assert (await g.graph.get_document(person.id, CollectionNames.PEOPLE.value))["email"] == person.email
    await g.graph.delete_nodes([person.id], CollectionNames.PEOPLE.value)


async def test_a_listing_for_a_user_keeps_only_what_the_access_check_allows(g: G, monkeypatch: pytest.MonkeyPatch) -> None:
    if g.backend == "neo4j":
        pytest.skip("the access check is replaced on the PostgreSQL provider only")
    from types import SimpleNamespace

    t = await _tree(g)
    group = await g.group("grp2")
    in_group = [await g.rec(f"g{i}", group=group) for i in range(3)]
    allowed = {t["root"].id, t["f1"].id, t["f3"].id, group.id, in_group[0].id, in_group[2].id}

    async def check_access(_u: str, _o: str, *, node_ids: list[str] = (), **_k: object) -> SimpleNamespace:
        return SimpleNamespace(node_ids={n for n in node_ids if n in allowed})
    monkeypatch.setattr(g.graph._p, "check_access", check_access)

    tree = await g.graph.get_records_by_parent_record(t["root"].id, g.conn, g.org, -1, user_key="u")
    assert {r.id for r in tree} == {t["root"].id, t["f1"].id, t["f3"].id}
    assert [r.id for r in await g.graph.get_records_by_parent_record(t["root"].id, g.conn, g.org, -1, user_key="u", limit=1, offset=1)] == sorted(
        [t["root"].id, t["f1"].id, t["f3"].id])[1:2]
    grouped = await g.graph.get_records_by_record_group(group.id, g.conn, g.org, -1, user_key="u")
    assert {r.id for r in grouped} == {in_group[0].id, in_group[2].id}
    allowed.discard(group.id)
    assert await g.graph.get_records_by_record_group(group.id, g.conn, g.org, -1, user_key="u") == []


NO_ORG_CALLS = {
    "get_record_by_weburl": ("u",), "get_record_by_issue_key": ("c", "K-1"), "get_record_by_conversation_index": ("c", "i", "t", "o", "u"),
    "get_record_by_path": ("c", ["a"], "g"), "get_records_by_file_path": ("c", "p"), "get_records_by_record_type": ("c", "FILE"),
    "get_records_by_status": ("o", "c", None), "get_records_by_weburls": (["u"], "o"), "get_records_linking_to_weburls": (["u"], "o", ["GITHUB"]),
    "get_issue_sites": ("o", ["JIRA"]), "find_duplicate_records": ("k", "m", "o"), "get_records_pending_duplicate_reconcile": (1, 1),
    "find_file_by_name_in_parent": (), "find_slack_burst_record_by_ts": ("c", "ch", "1"), "get_related_node_field": ("records/a", "nodeRelations", "records", "f"),
    "get_edges_from_node_with_target_name": ("records/a", "nodeRelations"), "get_child_record_ids_by_relation_type": ("a", "RELATED"),
    "get_parent_record_ids_by_relation_type": ("a", "RELATED"), "get_address_links_batch": (["a"], "o"), "get_linked_records": ("a", "o", "u", ["RELATED"]),
    "get_record_parent_adjacency": (["a"], "o"), "get_records_by_parent_record": ("a", "c", "o", 0), "get_records_by_record_group": ("g", "c", "o", 0),
    "link_record_to_group": ("a", "g"), "batch_upsert_people": ([],), "update_node_if_match": ("a", "records", {}, "f", 1),
    "remove_nodes_by_field": ("records", "f"), "get_file_record_by_id": ("a",), "get_record_owner_source_user_email": ("a",),
    "get_message_id_header_by_key": ("a", "mails"),
    "filter_linked_record_ids": (["a"],), "get_record_key_by_external_id": ("e", "c"),
    "get_failed_records_by_org": ("o", "c"), "get_documents_by_status": ("records", "s"), "get_departments": (), "delete_sync_points_by_connector_id": ("c",),
    "delete_connector_sync_edges": ("c",), "reap_stale_external_app_relations": ("c",),
    "find_next_queued_duplicate": ("a",), "get_app_needing_vector_membership_backfill": (), "page_records_for_vector_membership_backfill": ("c", None, 1),
    "_fetch_existing_file_names_in_parent": ("kb", None), "get_connector_instances_with_filters": ("apps",), "get_connector_stats": ("o", "c"),
    "_ref_link_rows": (["d"],),
}
NO_ORG_KW = {"find_file_by_name_in_parent": {"kb_id": "kb", "file_name": "f", "mime_type": "m"}, "link_record_to_group": {"inherit": True},
             "remove_nodes_by_field": {"field_value": 1}}
LOOKUP_METHODS = {n for n, f in vars(LookupsMixin).items() if not n.startswith("__") and callable(f)} - {"_lrows", "_at_addresses", "_keyed_rows", "_permitted", "_records_page", "_page_ids", "_key_by_external"}


def test_every_public_method_of_the_module_is_in_the_no_org_cases() -> None:
    assert LOOKUP_METHODS == set(NO_ORG_CALLS)


SWEEPS_BY_SYSTEM = {"get_records_pending_duplicate_reconcile", "get_app_needing_vector_membership_backfill"}


@pytest.mark.parametrize("name", sorted(NO_ORG_CALLS))
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*NO_ORG_CALLS[name], **NO_ORG_KW.get(name, {}))
    if name in SWEEPS_BY_SYSTEM:      # read for every organisation under a system context: the test below covers it
        return
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*NO_ORG_CALLS[name], **NO_ORG_KW.get(name, {}))


def raw(org: str, rid: str, **kw: object) -> dict:
    return {"_key": rid, "orgId": org, "recordName": rid.upper(), "externalRecordId": f"x-{rid}", "connectorId": "c1", "recordType": "FILE", "version": 1,
            "origin": "CONNECTOR", "connectorName": "GITHUB", "mimeType": "text/plain", "createdAtTimestamp": 1, "updatedAtTimestamp": 1, **kw}


async def test_cross_references_are_found_both_ways_and_only_between_live_records_of_the_org(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())

    async def check_access(_self: object, _u: str, _o: str, *, node_ids: list[str] = (), **_k: object) -> SimpleNamespace:
        return SimpleNamespace(node_ids=set(node_ids))
    monkeypatch.setattr(StandInProvider, "check_access", check_access)
    await p.batch_upsert_nodes([raw(p.org, "a"), raw(p.org, "b"), raw(p.org, "gone", isDeleted=True), raw("other", "foreign")], "records")
    link = {"from_collection": "records", "to_collection": "records", "relationshipType": "REFERENCE", "matchedBy": "url"}
    await p.batch_create_edges([{**link, "from_id": "a", "to_id": "b"}, {**link, "from_id": "gone", "to_id": "a"}, {**link, "from_id": "a", "to_id": "foreign"}], "nodeRelations")
    got = await p.get_address_links_batch(["a", "b", "a"], p.org)
    assert got == {"a": {"mentioned_in": [], "links_to": ["b"]}, "b": {"mentioned_in": ["a"], "links_to": []}}
    assert await p.get_address_links_batch(["a"], "") == {} and await p.get_address_links_batch([], p.org) == {}
    linked = await p.get_linked_records("a", p.org, "u", ["REFERENCE"])
    assert [x["id"] for x in linked] == ["b"]                                      # listed first, then filtered by the access check


async def test_urls_issue_sites_threads_duplicates_and_removal_by_a_field(pg_db) -> None:
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    await p.batch_upsert_nodes([raw(p.org, "t1", recordType="TICKET", connectorName="JIRA", webUrl="https://x.atlassian.net/browse/K-1"),
                                raw(p.org, "t2", recordType="TICKET", connectorName="JIRA", webUrl="https://y.atlassian.net/browse/K-2"),
                                raw(p.org, "lnk", recordType="LINK", connectorName="GITHUB", webUrl="https://z/u"),
                                raw(p.org, "host", connectorName="GITHUB"), raw(p.org, "d1", md5Checksum="m", duplicateReconcilePending=True, duplicateReconcileDueAt=5),
                                raw(p.org, "d2", md5Checksum="m")], "records")
    await p.batch_create_edges([{"from_id": "host", "from_collection": "records", "to_id": "lnk", "to_collection": "records", "relationshipType": "PARENT_CHILD"}], "nodeRelations")
    assert await p.get_issue_sites(p.org, ["JIRA"]) == ["https://x.atlassian.net", "https://y.atlassian.net"] and await p.get_issue_sites(p.org, []) == []
    assert (await p.get_record_by_issue_key("c1", "K-2")).id == "t2" and await p.get_record_by_issue_key("c1", "K-9") is None
    assert await p.get_records_linking_to_weburls(["https://z/u"], p.org, ["GITHUB"]) == ["host"]
    assert await p.get_records_linking_to_weburls(["https://z/u"], p.org, []) == []
    assert [d["id"] for d in await p.find_duplicate_records("d1", "m", p.org)] == ["d2"]
    pending = await p.get_records_pending_duplicate_reconcile(10, 5)
    assert pending == [{"_key": "d1", "orgId": p.org, "duplicateReconcileAttempts": None, "duplicateReconcileDueAt": 5}] and await p.get_records_pending_duplicate_reconcile(3, 5) == []
    await p.batch_create_edges([{"from_id": "t1", "from_collection": "records", "to_id": "t2", "to_collection": "records", "relationshipType": "REFERENCE"}], "nodeRelations")
    assert await p.remove_nodes_by_field("records", "recordType", field_value="TICKET") == 2
    assert await p.get_existing_record_keys(["t1", "t2", "host"]) == {"host"} and await p.get_edges_from_node("records/t1", "nodeRelations") == []


async def test_a_null_match_value_matches_nothing_in_remove_and_update(pg_db) -> None:
    """A node without the field is not 'null': Neo4j's ``n.f = null`` is never true, so nothing is removed and nothing is replaced."""
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    await p.batch_upsert_nodes([raw(p.org, "a"), raw(p.org, "b", flag="x")], "records")
    assert await p.remove_nodes_by_field("records", "flag", field_value=None) == 0
    assert await p.get_existing_record_keys(["a", "b"]) == {"a", "b"}
    assert await p.update_node_if_match("a", "records", {"recordName": "changed"}, "nofield", None) is False
    assert (await p.get_document("a", "records"))["recordName"] == "A"
    assert await p.remove_nodes_by_field("records", "flag", field_value="x") == 1


async def test_removing_a_category_node_by_field_takes_its_category_edges_with_it(pg_db) -> None:
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    await p.batch_upsert_nodes([{"_key": "c", "orgId": p.org, "name": "c"}], "categories")
    await p.batch_upsert_nodes([{"_key": "s", "orgId": p.org, "name": "s"}], "subcategories1")
    await p.batch_create_edges([{"from_id": "s", "from_collection": "subcategories1", "to_id": "c", "to_collection": "categories"}], "interCategoryRelations")
    assert len(await p.get_edges_from_node("subcategories1/s", "interCategoryRelations")) == 1
    assert await p.remove_nodes_by_field("subcategories1", "name", field_value="s") == 1
    assert await p.get_edges_from_node("subcategories1/s", "interCategoryRelations") == []


async def test_removing_records_by_field_takes_their_type_documents_and_blocks(pg_db) -> None:
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    await p.batch_upsert_nodes([raw(p.org, "gone", flag="x"), raw(p.org, "kept")], "records")
    await p.batch_upsert_nodes([{"_key": "gone", "orgId": p.org}], "files")
    await p.batch_upsert_nodes([{"_key": "blk", "orgId": p.org, "recordId": "gone"}, {"_key": "blk2", "orgId": p.org, "recordId": "kept"}], "blocks")
    await p.batch_create_edges([{"from_id": "gone", "from_collection": "records", "to_id": "gone", "to_collection": "files"}], "isOfType")
    assert await p.remove_nodes_by_field("records", "flag", field_value="x") == 1
    assert await p.get_document("gone", "files") is None and await p.get_document("blk", "blocks") is None
    assert await p.get_document("blk2", "blocks") is not None and await p.get_existing_record_keys(["gone", "kept"]) == {"kept"}


async def test_a_status_page_of_limit_zero_is_empty_not_everything(pg_db) -> None:
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    await p.batch_upsert_nodes([raw(p.org, "a", indexingStatus="QUEUED")], "records")
    await p.batch_upsert_nodes([{"_key": "a", "isFile": True, "extension": "txt", "mimeType": "text/plain"}], "files")
    await p.batch_create_edges([{"from_id": "a", "from_collection": "records", "to_id": "a", "to_collection": "files"}], "isOfType")
    assert await p.get_records_by_status(p.org, "c1", ["QUEUED"], limit=0) == []
    assert [r.id for r in await p.get_records_by_status(p.org, "c1", ["QUEUED"])] == ["a"]


async def test_no_lookup_returns_another_orgs_record_or_a_trashed_one(pg_db) -> None:
    a, b = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org()), OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    for p in (a, b):
        await p.batch_upsert_nodes([raw(p.org, "r", webUrl="https://w/1", md5Checksum="m", recordType="TICKET", externalRevisionId="rv", virtualRecordId="v",
                                        connectorName="JIRA"),
                                    raw(p.org, "r2", webUrl="https://w/1", md5Checksum="m", virtualRecordId="v2"), raw(p.org, "kid", externalParentId="x-r")], "records")
        await p.batch_create_edges([{"from_id": "r", "from_collection": "records", "to_id": "kid", "to_collection": "records", "relationshipType": "PARENT_CHILD"}], "nodeRelations")
        await p.batch_upsert_nodes([{"_key": "g", "orgId": p.org, "connectorId": "c1"}], "recordGroups")
        await p.batch_create_edges([{"from_id": "kid", "from_collection": "records", "to_id": "g", "to_collection": "recordGroups"}], "belongsTo")
    # b holds records of the same ids; a asks with b's org id as the argument, and with its own
    assert await a.get_record_by_weburl("https://w/1", b.org) is None
    assert await a.get_records_by_weburls(["https://w/1"], b.org) == []
    assert await a.find_duplicate_records("r2", "m", b.org) == []
    assert await a.get_records_by_parent_record("r", "c1", b.org, -1) == [] and await a.get_records_by_record_group("g", "c1", b.org, -1) == []
    assert await a.get_address_links_batch(["r"], b.org) == {"r": {"mentioned_in": [], "links_to": []}} and await a.get_linked_records("r", b.org, "u", ["X"]) == []
    adj = await a.get_record_parent_adjacency(["kid"], b.org)
    assert adj["nodes"] == {} or all(n["id"] != "r" for n in adj["nodes"].values())
    assert await a.get_issue_sites(b.org, ["JIRA"]) == [] and await a.get_records_linking_to_weburls(["https://w/1"], b.org, ["JIRA"]) == []
    # a trashed record is not found by address, duplicate or issue key
    await a.batch_update_nodes([{"id": "r", "isDeleted": True}], "records")
    assert (await a.get_record_by_weburl("https://w/1", a.org)).id == "r2" and [d["id"] for d in await a.find_duplicate_records("r2", "m", a.org)] == []
    assert [d["id"] for d in await a.get_records_by_weburls(["https://w/1"], a.org)] == ["r2"]


async def test_the_pending_reconciles_of_every_org_come_back_with_their_org_under_a_system_context(pg_db) -> None:
    from app.core import org_context

    orgs = [new_org(), new_org()]
    key = f"d-{uuid.uuid4().hex}"
    for o in orgs:
        p = OrgBound(StandInProvider(logging.getLogger("lk"), pg_db), o)
        await p.batch_upsert_nodes([{"_key": o, "name": "o"}], "organizations")
        await p.batch_upsert_nodes([raw(o, key, duplicateReconcilePending=True, duplicateReconcileDueAt=5)], "records")
    sys_p = StandInProvider(logging.getLogger("lk"), pg_db)
    with org_context.system("test.pending_reconciles"):
        got = [r for r in await sys_p.get_records_pending_duplicate_reconcile(10, 1000) if r["_key"] == key]
    assert sorted(r["orgId"] for r in got) == sorted(orgs)
    with org_context.for_org(orgs[0], job="test"):
        assert [r["orgId"] for r in await sys_p.get_records_pending_duplicate_reconcile(10, 1000) if r["_key"] == key] == [orgs[0]]
    with pytest.raises(org_context.MissingOrgContext):
        await sys_p.get_records_pending_duplicate_reconcile(10, 1000)


# The provider's statements before the address lookups read through graph.records_by_weburl and graph.ticket_sites (graph_0017): each read every record of the org.
OLD_BY_WEBURL = """SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'webUrl' = $2 AND ($3::text IS NULL OR n.doc->>'orgId' = $3)
    AND n.doc->>'recordType' <> 'LINK' AND coalesce((n.doc->>'isDeleted')::boolean, false) = false"""
OLD_BY_WEBURLS = """SELECT id, "recordType", "connectorName", "webUrl", "isDependentNode" FROM (
      SELECT n.id, n.doc->>'recordType' AS "recordType", n.doc->>'connectorName' AS "connectorName", n.doc->>'webUrl' AS "webUrl",
             coalesce((n.doc->>'isDependentNode')::boolean, false) AS "isDependentNode",
             row_number() OVER (PARTITION BY n.doc->>'webUrl' ORDER BY coalesce((n.doc->>'isDependentNode')::boolean, false), n.id) AS rn
      FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'webUrl' = ANY($2::text[]) AND n.doc->>'orgId' = $3
        AND n.doc->>'recordType' <> 'LINK' AND NOT coalesce((n.doc->>'isPlaceholder')::boolean, false) AND ($5 OR coalesce((n.doc->>'isDeleted')::boolean, false) = false)) q
    WHERE rn <= $4"""
OLD_LINKING = """SELECT DISTINCT h.id FROM graph.nodes link
    JOIN graph.edges e ON e.org_id = link.org_id AND e.coll = 'nodeRelations' AND e.to_coll = 'records' AND e.to_id = link.id
      AND e.doc->>'relationshipType' = 'PARENT_CHILD' AND e.from_coll = 'records'
    JOIN graph.nodes h ON h.org_id = e.org_id AND h.coll = 'records' AND h.id = e.from_id
    WHERE link.org_id = $1 AND link.coll = 'records' AND link.doc->>'webUrl' = ANY($2::text[]) AND link.doc->>'orgId' = $3
      AND link.doc->>'recordType' = 'LINK' AND link.doc->>'connectorName' = ANY($4::text[]) AND coalesce((link.doc->>'isDeleted')::boolean, false) = false
      AND h.doc->>'orgId' = $3 AND coalesce((h.doc->>'isDeleted')::boolean, false) = false AND ($5::text IS NULL OR h.id > $5)
    ORDER BY h.id LIMIT $6"""
OLD_SITES = """SELECT site FROM (
      SELECT DISTINCT (regexp_match(n.doc->>'webUrl', '^(.*)/browse/'))[1] AS site FROM graph.nodes n
      WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'orgId' = $2 AND n.doc->>'recordType' = 'TICKET'
        AND n.doc->>'connectorName' = ANY($3::text[]) AND n.doc->>'webUrl' LIKE '%/browse/%' AND coalesce((n.doc->>'isDeleted')::boolean, false) = false) q
    WHERE site <> '' ORDER BY site LIMIT $4"""
LONG_URL = "https://long.example/" + "".join(f"{i:08x}{(i * 2654435761) % 2**32:08x}" for i in range(400))


async def _seed_web(p: OrgBound, holders_of: str) -> None:
    w = "https://w.example/p"
    rows = [raw(p.org, "page", webUrl=w, recordType="WEBPAGE"), raw(p.org, "att1", webUrl=w, isDependentNode=True), raw(p.org, "att0", webUrl=w, isDependentNode=True),
            raw(p.org, "page2", webUrl=w), raw(p.org, "slash", webUrl=w + "/"), raw(p.org, "query", webUrl=w + "?a=1"), raw(p.org, "gone", webUrl=w + "/gone", isDeleted=True),
            raw(p.org, "ph", webUrl=w + "/ph", isPlaceholder=True), raw(p.org, "long", webUrl=LONG_URL), raw(p.org, "empty", webUrl=""),
            raw(p.org, "owned-elsewhere", webUrl=w + "/o", orgId=holders_of), raw(p.org, "nourl"),
            raw(p.org, "lnk", webUrl=w, recordType="LINK", connectorName="SLACK WORKSPACE"), raw(p.org, "lnk2", webUrl=w + "/", recordType="LINK", connectorName="SLACK"),
            raw(p.org, "lnk-gh", webUrl=w, recordType="LINK", connectorName="GITHUB"), raw(p.org, "lnk-gone", webUrl=w + "/ph", recordType="LINK", connectorName="SLACK", isDeleted=True),
            *(raw(p.org, f"m{i}", recordType="MESSAGE", connectorName="SLACK WORKSPACE", isDeleted=i == 3) for i in range(6)),
            raw(p.org, "t1", recordType="TICKET", connectorName="JIRA", webUrl="https://x.atlassian.net/browse/K-1"),
            raw(p.org, "t2", recordType="TICKET", connectorName="JIRA DATA CENTER", webUrl="https://jira.corp.example/jira/browse/D-2"),
            raw(p.org, "t3", recordType="TICKET", connectorName="JIRA", webUrl="https://gone.atlassian.net/browse/G-1", isDeleted=True),
            raw(p.org, "t4", recordType="TICKET", connectorName="JIRA", webUrl="https://h.example/browse/x/browse/K-1")]
    await p.batch_upsert_nodes(rows, "records")
    pc = {"from_collection": "records", "to_collection": "records", "relationshipType": "PARENT_CHILD"}
    await p.batch_create_edges([{**pc, "from_id": f"m{i}", "to_id": t} for i, t in enumerate(["lnk", "lnk", "lnk2", "lnk", "lnk-gh", "lnk-gone"])], "nodeRelations")


async def test_the_address_lookups_answer_what_the_plain_statements_answered(pg_db) -> None:
    a, b = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org()), OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    await _seed_web(a, b.org)
    await _seed_web(b, a.org)
    w = "https://w.example/p"
    urls = [w, w + "/", w + "?a=1", w + "/gone", w + "/ph", w + "/o", LONG_URL, "", w.upper(), w + "x", "https://nowhere.example/"]
    calls = 0
    for p, other in ((a, b), (b, a)):
        for u in [*urls, None]:
            for owner in (p.org, other.org, None):
                old = {r["id"] for r in await p._lrows(OLD_BY_WEBURL, p.org, u, owner)}
                got = await p.get_record_by_weburl(u, owner)
                assert (got.id in old) if got else not old, (u, owner, got and got.id, old)
                calls += 1
        for batch in ([w], urls, urls[:4] + [w] * 3, [f"https://miss.example/{i}" for i in range(1100)] + [w, w + "/"]):
            for per in (1, 2, 50):
                for deleted in (False, True):
                    for owner in (p.org, other.org):
                        old = await p._lrows(OLD_BY_WEBURLS, p.org, list(dict.fromkeys(x for x in batch if x)), owner, per, deleted)
                        got = await p.get_records_by_weburls(batch, owner, per, include_deleted=deleted)
                        assert sorted(map(tuple, (dict(r).values() for r in old))) == sorted(map(tuple, (r.values() for r in got))), (len(batch), per, deleted)
                        calls += 1
        for batch in ([w], [w, w + "/"], urls, [w + "/ph"], [w, None], [None]):
            for names in (["SLACK WORKSPACE", "SLACK"], ["SLACK"], ["GITHUB"], ["JIRA"]):
                for owner in (p.org, other.org):
                    for limit in (1, 2, 100):
                        after = None
                        for _ in range(6):
                            old = [r["id"] for r in await p._lrows(OLD_LINKING, p.org, batch, owner, names, after, limit)]
                            assert await p.get_records_linking_to_weburls(batch, owner, names, limit, after) == old, (batch, names, owner, limit, after)
                            calls += 1
                            if len(old) < limit:
                                break
                            after = old[-1]
        for names in (["JIRA"], ["JIRA", "JIRA DATA CENTER"], ["GITHUB"], ["JIRA", "JIRA"], ["JIRA", None, ""], [None]):
            for owner in (p.org, other.org):
                for limit in (None, 0, 1, 3):
                    old = [r["site"] for r in await p._lrows(OLD_SITES, p.org, owner, names, limit)]
                    assert await p.get_issue_sites(owner, names, limit) == old, (names, owner, limit)
                    calls += 1
    assert calls > 400
    # the answers themselves, for the reader
    assert {r["id"] for r in await a.get_records_by_weburls([w], a.org, 50)} == {"page", "page2", "att0", "att1"}
    assert [r["id"] for r in await a.get_records_by_weburls([w], a.org, 1)] == ["page"]
    assert (await a.get_record_by_weburl(LONG_URL, a.org)).id == "long"
    assert await a.get_records_linking_to_weburls([w, w + "/"], a.org, ["SLACK WORKSPACE", "SLACK"]) == ["m0", "m1", "m2"]
    assert await a.get_issue_sites(a.org, ["JIRA", "JIRA DATA CENTER"]) == sorted(["https://x.atlassian.net", "https://jira.corp.example/jira", "https://h.example/browse/x"])


# fresh: this org's rows are newer than the statistics; analysed: the statistics are taken now, when the org holds one relation edge (the edges' leaf it lives
# in can hold almost nothing else, as a test database's leaves and a new install's do), so a scan of the leaf looks cheaper than a probe of its index per link.
@pytest.mark.parametrize("stats", ["fresh", pytest.param("analysed", marks=pytest.mark.pg_table_lock)])      # analysed changes the planner statistics of shared tables
async def test_an_address_held_by_more_records_than_the_probe_returns_is_still_read_whole(pg_db, monkeypatch: pytest.MonkeyPatch, stats: str) -> None:
    """graph.records_by_weburl returns at most 10,000 records per address, in no order. At an address that holds more (here 10,050 link records,
    some trashed or placeholders, inserted before the ones that matter), the eligible record and the link holder are still found, as the plain
    statements found them. The holders are read by key from each link: joined, on an org the statistics did not know yet, the planner crossed every
    record of the org with every link record and the statement ran past its timeout; with statistics that say the edges' leaf is nearly empty, it
    scanned the leaf once per link."""
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    sent: list[tuple[str, tuple]] = []
    real = Tx.fetch

    async def spy(self: Tx, sql: str, *args: object) -> list:
        if "pipeshub:graph.get_records_linking_to_weburls" in sql:
            sent.append((sql, args))
        return await real(self, sql, *args)
    monkeypatch.setattr(Tx, "fetch", spy)
    profiled: list[str] = []
    real_row = Tx.fetchrow

    async def spy_row(self: Tx, sql: str, *args: object) -> object:
        if "pipeshub:graph.linking_profile" in sql:
            profiled.append(sql)
        return await real_row(self, sql, *args)
    monkeypatch.setattr(Tx, "fetchrow", spy_row)
    u = "https://crowded.example/p"
    await p._lrows("""INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'records', 'crowd-' || lpad(i::text, 5, '0'), jsonb_build_object('orgId', $1::text,
        'webUrl', $2::text, 'recordType', 'LINK', 'connectorName', 'GITHUB', 'isDeleted', i % 7 = 0, 'isPlaceholder', i % 11 = 0) FROM generate_series(1, 10050) i""",
                   p.org, u, write=True)
    await p.batch_upsert_nodes([raw(p.org, "zz-page", webUrl=u), raw(p.org, "zz-link", webUrl=u, recordType="LINK", connectorName="SLACK WORKSPACE"),
                                raw(p.org, "zz-holder", recordType="MESSAGE")], "records")
    await p.batch_create_edges([{"from_id": "zz-holder", "from_collection": "records", "to_id": "zz-link", "to_collection": "records",
                                 "relationshipType": "PARENT_CHILD"}], "nodeRelations")
    if stats == "analysed":
        await _admin("ANALYZE graph.edges")
    assert (await p.get_record_by_weburl(u, p.org)).id == "zz-page"
    assert [r["id"] for r in await p.get_records_by_weburls([u], p.org, 50, include_deleted=True)] == ["zz-page"]
    assert await p.get_records_linking_to_weburls([u, "https://other.example/"], p.org, ["SLACK WORKSPACE"]) == ["zz-holder"]
    old = await p._lrows(OLD_BY_WEBURLS, p.org, [u], p.org, 50, True)
    assert [dict(r) for r in old] == await p.get_records_by_weburls([u], p.org, 50, include_deleted=True)
    assert await p.get_records_linking_to_weburls([u], p.org, ["SLACK WORKSPACE", "GITHUB"]) == ["zz-holder"]      # the crowd's links have no holder
    assert len(sent) == 4, len(sent)        # each call: the probe's statement, then (capped) the plain one
    assert len(profiled) == len(sent)        # each of them under the profile
    for sql, args in sent:
        async def explain(tx, sql=sql, args=args) -> str:
            await tx.fetchrow(LINKING_PROFILE)      # under the profile the statement runs with
            return await tx._con.fetchval("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, *args)
        plan = plan_gate.root(await p._q(explain, None))
        assert not (bad := plan_gate.unkeyed(plan, "edges", r"\b(to_id|from_id) = ")), (sql[:60], bad)
        # the link records are read once (the org's, by the plain statement); every other record by its id
        assert not (bad := [n.get("Index Cond") for n in plan_gate.scans(plan, "nodes") if n.get("Actual Loops", 1) > 1 and not plan_gate.keyed(n, r"\bid = ")]), \
            (sql[:60], bad)


async def test_a_link_holder_read_in_a_callers_transaction_leaves_its_settings_as_they_were(pg_db) -> None:
    """The link holders' statements run with sequential and bitmap scans off: in a caller's transaction the two are put back after the read, so what the
    caller runs next plans as it would have."""
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    t = await p.begin_transaction([], [])
    try:
        assert await p.get_records_linking_to_weburls(["https://nowhere.example/"], p.org, ["SLACK WORKSPACE"], transaction=t) == []
        got = await p._q(lambda tx: tx.fetchrow("SELECT current_setting('enable_seqscan') AS s, current_setting('enable_bitmapscan') AS b"), t)
    finally:
        await p.rollback_transaction(t)
    assert (got["s"], got["b"]) == ("on", "on"), dict(got)


async def test_a_link_holder_read_that_loses_its_connection_is_retried(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """A standalone read whose connection drops during the lookup (after the profile's statement) is read again in a new unit, as any read of one statement is:
    the unit is a read, so the statement it had already run takes nothing from the retry."""
    import asyncpg
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    u = "https://dropped.example/p"
    await p.batch_upsert_nodes([raw(p.org, "dz-link", webUrl=u, recordType="LINK", connectorName="SLACK WORKSPACE"), raw(p.org, "dz-holder", recordType="MESSAGE")], "records")
    await p.batch_create_edges([{"from_id": "dz-holder", "from_collection": "records", "to_id": "dz-link", "to_collection": "records",
                                 "relationshipType": "PARENT_CHILD"}], "nodeRelations")
    dropped: list[str] = []
    real = Tx._run

    async def drop(self: Tx, op: str, sql: str, *args: object, internal: bool = False) -> object:
        if "pipeshub:graph.get_records_linking_to_weburls" in sql and not dropped:
            dropped.append(sql)
            kind = type(self._con)      # the pool's proxy: its methods are its class's
            con_fetch = kind.fetch

            async def lost(*a: object, **k: object) -> object:
                raise asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")
            monkeypatch.setattr(kind, "fetch", lost)
            try:
                return await real(self, op, sql, *args, internal=internal)
            finally:
                monkeypatch.setattr(kind, "fetch", con_fetch)
        return await real(self, op, sql, *args, internal=internal)
    monkeypatch.setattr(Tx, "_run", drop)
    assert await p.get_records_linking_to_weburls([u], p.org, ["SLACK WORKSPACE"]) == ["dz-holder"]
    assert dropped


async def test_more_addresses_than_one_probe_takes_are_read_in_one_statement(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """graph.records_by_weburl takes 1,000 addresses a call. 2,502 are read by one statement, so from one snapshot, outside a transaction and inside
    the caller's (whose separate statements would each read their own snapshot)."""
    p = OrgBound(StandInProvider(logging.getLogger("x"), pg_db), new_org())
    await _seed_web(p, new_org())
    w = "https://w.example/p"
    batch = [f"https://miss.example/{i}" for i in range(2500)] + [w, w + "/"]
    want = sorted(map(tuple, (dict(r).values() for r in await p._lrows(OLD_BY_WEBURLS, p.org, batch, p.org, 50, False))))
    sent: list[str] = []
    real = Tx.fetch

    async def spy(self: Tx, sql: str, *args: object) -> list:
        if "pipeshub:graph.get_records_by_weburls" in sql:
            sent.append(sql)
        return await real(self, sql, *args)
    monkeypatch.setattr(Tx, "fetch", spy)
    outside = await p.get_records_by_weburls(batch, p.org, 50)
    t = await p.begin_transaction([], [])
    try:
        inside = await p.get_records_by_weburls(batch, p.org, 50, transaction=t)
    finally:
        await p.rollback_transaction(t)
    assert len(sent) == 2, len(sent)
    assert sorted(map(tuple, (r.values() for r in outside))) == sorted(map(tuple, (r.values() for r in inside))) == want and want
