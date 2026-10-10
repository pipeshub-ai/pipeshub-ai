"""A full sync tags its connector's edges and sweeps the ones still tagged when it
succeeds. Every write-path check of the form "this edge is already there, skip"
must therefore not count a tagged edge, or the skipped edge is swept (R1-01, R1-11).

The graph here is an in-memory stand-in with the providers' tag rules: a write
clears the tag, mark tags every edge touching the connector's nodes, sweep
deletes what still carries the generation. The two-backend version of the same
flows is in tests/integration/graph_permissions/test_write_path.py.
"""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import (
    AppUser,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.common.sync_sweep import PENDING_SWEEP, full_sync_running

ORG = "org-1"
CONNECTOR = "conn-1"
GENERATION = 1_760_000_000_000

RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
USERS = CollectionNames.USERS.value
APPS = CollectionNames.APPS.value
ORGS = CollectionNames.ORGS.value
BELONGS_TO = CollectionNames.BELONGS_TO.value
NODE_RELATIONS = CollectionNames.NODE_RELATIONS.value
INHERIT = CollectionNames.INHERIT_PERMISSIONS.value
PERMISSION = CollectionNames.PERMISSION.value


def _id(collection: str, key: str) -> str:
    return f"{collection}/{key}"


class FakeGraph:
    """Only what the paths under test call, with the providers' tag semantics."""

    def __init__(self) -> None:
        self.logger = logging.getLogger("fake-graph")
        self.edges: dict[tuple[str, str, str], dict] = {}
        self.records: dict[str, WebpageRecord] = {}
        self.groups: dict[str, RecordGroup] = {}
        self.users: dict[str, User] = {}

    # -- edges -------------------------------------------------------------
    def put(self, collection: str, from_id: str, to_id: str, **props) -> None:
        self.edges[(collection, from_id, to_id)] = {**props, "_from": from_id, "_to": to_id}

    def has(self, collection: str, from_id: str, to_id: str) -> bool:
        return (collection, from_id, to_id) in self.edges

    def _drop(self, collection: str, from_id: str, to_id: str) -> None:
        self.edges.pop((collection, from_id, to_id), None)

    @staticmethod
    def _ends(edge: dict) -> tuple[str, str]:
        if "_from" in edge:
            return edge["_from"], edge["_to"]
        return _id(edge["from_collection"], edge["from_id"]), _id(edge["to_collection"], edge["to_id"])

    async def get_edge(self, from_id, from_collection, to_id, to_collection, collection, transaction=None):
        edge = self.edges.get((collection, _id(from_collection, from_id), _id(to_collection, to_id)))
        return dict(edge) if edge else None

    async def get_edges_from_node(self, node_id, edge_collection, transaction=None):
        return [dict(e) for (c, f, _), e in self.edges.items() if c == edge_collection and f == node_id]

    async def get_edges_to_node(self, node_id, edge_collection, transaction=None):
        return [dict(e) for (c, _, t), e in self.edges.items() if c == edge_collection and t == node_id]

    async def batch_create_edges(self, edges, collection, transaction=None):
        for edge in edges:
            props = {k: v for k, v in edge.items() if k not in ("_from", "_to") and not k.endswith(("_id", "_collection"))}
            self.put(collection, *self._ends(edge), **props)
        return True

    async def create_edges_if_absent(self, edges, collection, transaction=None):
        for edge in edges:
            if not self.has(collection, *self._ends(edge)):
                await self.batch_create_edges([edge], collection)

    async def delete_edge(self, from_id, from_collection, to_id, to_collection, collection, transaction=None):
        self._drop(collection, _id(from_collection, from_id), _id(to_collection, to_id))
        return True

    async def create_record_relation(self, from_record_id, to_record_id, relation_type, transaction=None):
        self.put(NODE_RELATIONS, _id(RECORDS, from_record_id), _id(RECORDS, to_record_id), relationshipType=relation_type)

    async def link_record_to_group(self, record_id, record_group_id, *, inherit, leaving_group_id=None,
                                   browse_root=None, transaction=None):
        record, group = _id(RECORDS, record_id), _id(GROUPS, record_group_id)
        if leaving_group_id:
            self._drop(BELONGS_TO, record, _id(GROUPS, leaving_group_id))
            self._drop(INHERIT, record, _id(GROUPS, leaving_group_id))
        self.put(BELONGS_TO, record, group)
        if inherit:
            self.put(INHERIT, record, group)
        elif inherit is False:
            self._drop(INHERIT, record, group)
        if browse_root:
            self.put(NODE_RELATIONS, group, record, relationshipType="PARENT_CHILD")
        elif browse_root is False:
            self._drop(NODE_RELATIONS, group, record)

    async def replace_record_permissions(self, record_id, edges, record_group_id, *, inherit, transaction=None):
        record = _id(RECORDS, record_id)
        for key in [k for k in self.edges if k[0] == PERMISSION and k[2] == record]:
            del self.edges[key]
        await self.batch_create_edges(edges, PERMISSION)
        if record_group_id:
            if inherit:
                self.put(INHERIT, record, _id(GROUPS, record_group_id))
            else:
                self._drop(INHERIT, record, _id(GROUPS, record_group_id))

    async def mark_connector_sync_edges(self, connector_id, generation, transaction=None):
        nodes = self._connector_nodes(connector_id)
        touched = [e for (_, f, t), e in self.edges.items() if f in nodes or t in nodes]
        for edge in touched:
            edge[PENDING_SWEEP] = generation
        return len(touched), True

    async def sweep_connector_sync_edges(self, connector_id, generation, transaction=None):
        nodes = self._connector_nodes(connector_id)
        stale = [k for k, e in self.edges.items() if (k[1] in nodes or k[2] in nodes)
                 and e.get(PENDING_SWEEP) == generation]
        for key in stale:
            del self.edges[key]
        return len(stale), True

    def _connector_nodes(self, connector_id: str) -> set[str]:
        return ({_id(RECORDS, r.id) for r in self.records.values() if r.connector_id == connector_id}
                | {_id(GROUPS, g.id) for g in self.groups.values() if g.connector_id == connector_id}
                | {_id(APPS, connector_id)})

    # -- nodes -------------------------------------------------------------
    async def get_record_by_external_id(self, connector_id, external_id, transaction=None, visibility=None):
        return next((r.model_copy() for r in self.records.values()
                     if r.connector_id == connector_id and r.external_record_id == external_id), None)

    async def get_record_group_by_external_id(self, connector_id, external_id, transaction=None):
        return next((g.model_copy() for g in self.groups.values()
                     if g.connector_id == connector_id and g.external_group_id == external_id), None)

    async def get_record_group_by_id(self, id, transaction=None):
        return self.groups.get(id)

    async def batch_upsert_records(self, records, transaction=None, release_trashed_external_ids=False):
        for record in records:
            self.records[record.id] = record.model_copy()

    async def batch_upsert_record_groups(self, groups, transaction=None):
        for group in groups:
            self.groups[group.id] = group.model_copy()

    async def batch_update_nodes(self, nodes, collection, transaction=None):
        return True

    async def get_user_by_email(self, email, transaction=None, raise_on_error=False):
        return self.users.get(email.lower())

    # -- transactions ------------------------------------------------------
    async def begin_transaction(self, read=None, write=None, **_):
        return "txn"

    async def commit_transaction(self, txn):
        return None

    async def rollback_transaction(self, txn):
        return None

    def is_write_conflict(self, error) -> bool:
        return False

    def is_transient_error(self, error) -> bool:
        return False


def _processor(graph: FakeGraph) -> DataSourceEntitiesProcessor:
    processor = DataSourceEntitiesProcessor(MagicMock(), GraphDataStore(MagicMock(), graph), AsyncMock())
    processor.org_id = ORG
    processor.messaging_producer = AsyncMock()
    return processor


def _record(key: str, external_id: str, *, parent: str | None = None) -> WebpageRecord:
    return WebpageRecord(
        id=key, org_id=ORG, record_name=f"Page {external_id}", external_record_id=external_id,
        record_type=RecordType.CONFLUENCE_PAGE, parent_external_record_id=parent,
        parent_record_type=RecordType.CONFLUENCE_PAGE if parent else None,
        external_record_group_id="space-1", record_group_type=RecordGroupType.CONFLUENCE_SPACES,
        version=1, origin=OriginTypes.CONNECTOR, connector_name=Connectors.CONFLUENCE,
        connector_id=CONNECTOR, inherit_permissions=True,
    )


def _synced_tree() -> tuple[FakeGraph, WebpageRecord, WebpageRecord]:
    """A space with a page and a sub-page, one user granted on the sub-page, as a
    sync before this one left it."""
    graph = FakeGraph()
    graph.groups["g1"] = RecordGroup(
        id="g1", org_id=ORG, name="Space", external_group_id="space-1", connector_name=Connectors.CONFLUENCE,
        connector_id=CONNECTOR, group_type=RecordGroupType.CONFLUENCE_SPACES,
    )
    parent, child = _record("p1", "page-1"), _record("c1", "page-2", parent="page-1")
    for record in (parent, child):
        graph.records[record.id] = record.model_copy(update={"record_group_id": "g1"})
    graph.users["ana@example.com"] = User(id="u1", email="ana@example.com", org_id=ORG)
    group, p, c = _id(GROUPS, "g1"), _id(RECORDS, "p1"), _id(RECORDS, "c1")
    graph.put(BELONGS_TO, p, group)
    graph.put(BELONGS_TO, c, group)
    graph.put(INHERIT, p, group)
    graph.put(INHERIT, c, p)
    graph.put(NODE_RELATIONS, group, p, relationshipType="PARENT_CHILD")
    graph.put(NODE_RELATIONS, p, c, relationshipType="PARENT_CHILD")
    graph.put(PERMISSION, _id(USERS, "u1"), c, role="READER", type="USER")
    return graph, parent, child


def _grant() -> list[Permission]:
    return [Permission(email="ana@example.com", type=PermissionType.READ, entity_type=EntityType.USER)]


@pytest.mark.asyncio
async def test_a_permission_only_update_keeps_the_records_structure_through_the_sweep() -> None:
    """R1-01: Dropbox (and others) send an unchanged existing record only through
    on_updated_record_permissions. Its BELONGS_TO was tagged, read as present, and
    the structural restore skipped, so the sweep took group, parent and hierarchy."""
    graph, parent, child = _synced_tree()
    processor = _processor(graph)
    await graph.mark_connector_sync_edges(CONNECTOR, GENERATION)

    with full_sync_running(GENERATION):
        await processor.on_updated_record_permissions(parent, [])
        await processor.on_updated_record_permissions(child, _grant())
    await graph.sweep_connector_sync_edges(CONNECTOR, GENERATION)

    group, p, c = _id(GROUPS, "g1"), _id(RECORDS, "p1"), _id(RECORDS, "c1")
    assert graph.has(BELONGS_TO, p, group) and graph.has(BELONGS_TO, c, group)
    assert graph.has(NODE_RELATIONS, group, p), "the group lists its root page"
    assert graph.has(NODE_RELATIONS, p, c), "the page lists its sub-page"
    assert graph.has(INHERIT, p, group) and graph.has(INHERIT, c, p)
    assert graph.has(PERMISSION, _id(USERS, "u1"), c)
    assert all(PENDING_SWEEP not in edge for edge in graph.edges.values()), "every survivor was written again"


@pytest.mark.asyncio
async def test_outside_a_full_sync_a_tag_left_behind_counts_as_stored() -> None:
    """R1-24: a tag a crashed sync left is not "not written yet" to a later sync."""
    graph, parent, _ = _synced_tree()
    processor = _processor(graph)
    await graph.mark_connector_sync_edges(CONNECTOR, GENERATION)
    graph._drop(NODE_RELATIONS, _id(GROUPS, "g1"), _id(RECORDS, "p1"))

    await processor.on_updated_record_permissions(parent, [])

    assert not graph.has(NODE_RELATIONS, _id(GROUPS, "g1"), _id(RECORDS, "p1")), \
        "BELONGS_TO is there, so no restore runs"


@pytest.mark.asyncio
@pytest.mark.parametrize("upgrade_only", [False, True])
async def test_a_grant_only_the_tag_keeps_is_written_again(upgrade_only: bool) -> None:
    """Skipped as present, the grant kept its tag and the sweep took it."""
    graph, _, _ = _synced_tree()
    processor = _processor(graph)
    graph.put(PERMISSION, _id(USERS, "u1"), _id(RECORDS, "p1"), role="OWNER", type="USER")
    await graph.mark_connector_sync_edges(CONNECTOR, GENERATION)

    with full_sync_running(GENERATION):
        await processor.upsert_permission_edge(
            "u1", USERS, "p1", RECORDS,
            Permission(email="ana@example.com", type=PermissionType.READ, entity_type=EntityType.USER),
            upgrade_only=upgrade_only,
        )
    await graph.sweep_connector_sync_edges(CONNECTOR, GENERATION)

    edge = graph.edges.get((PERMISSION, _id(USERS, "u1"), _id(RECORDS, "p1")))
    assert edge is not None and edge["role"] == "READER", "the source's role, as a write after a wipe would store"


@pytest.mark.asyncio
async def test_a_stub_group_whose_only_parent_edge_is_tagged_is_placed_under_its_app() -> None:
    """R1-11: the repair read the tagged parent edge as present and wrote only the
    org edge; the sweep took the parent edge, and with orgId set the repair never ran again."""
    graph, _, _ = _synced_tree()
    processor = _processor(graph)
    stub = RecordGroup(
        id="g2", org_id="", name="Stub", external_group_id="space-2", connector_name=Connectors.CONFLUENCE,
        connector_id=CONNECTOR, group_type=RecordGroupType.CONFLUENCE_SPACES,
    )
    graph.groups["g2"] = stub
    graph.put(BELONGS_TO, _id(GROUPS, "g2"), _id(GROUPS, "g1"))
    await graph.mark_connector_sync_edges(CONNECTOR, GENERATION)

    with full_sync_running(GENERATION):
        async with processor.data_store_provider.transaction() as tx_store:
            await processor._repair_detached_stub_group(tx_store, stub.model_copy())
    await graph.sweep_connector_sync_edges(CONNECTOR, GENERATION)

    assert graph.has(BELONGS_TO, _id(GROUPS, "g2"), _id(APPS, CONNECTOR))
    assert graph.has(NODE_RELATIONS, _id(APPS, CONNECTOR), _id(GROUPS, "g2"))


@pytest.mark.asyncio
async def test_keeping_a_stored_inheritance_reads_a_tagged_inherit_edge() -> None:
    """A connector that cannot read a node's permissions keeps its stored
    inheritance; to that question a tagged edge is what is stored."""
    graph, _, _ = _synced_tree()
    processor = _processor(graph)
    await graph.mark_connector_sync_edges(CONNECTOR, GENERATION)

    with full_sync_running(GENERATION):
        assert await processor.inheritance_when_unreadable(RECORDS, "c1") is True


@pytest.mark.asyncio
async def test_deleting_a_group_collects_records_whose_membership_is_tagged() -> None:
    graph, _, _ = _synced_tree()
    await graph.mark_connector_sync_edges(CONNECTOR, GENERATION)

    with full_sync_running(GENERATION):
        async with _processor(graph).data_store_provider.transaction() as tx_store:
            groups, records = await DataSourceEntitiesProcessor._collect_group_subtree(tx_store, "g1")

    assert groups == ["g1"] and set(records) == {"p1", "c1"}
