"""The knowledge hub's parent of a node, against a real Neo4j and a real ArangoDB.

The tree is written through the real ``DataSourceEntitiesProcessor``, as a connector writes it. ArangoDB
answered no parent for every connector record: the branch that reads it is a subquery, whose value is an
array, and an array names no document.

Requires the graph stack from deployment/docker-compose/docker-compose.integration.graph-db.yml
(NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD; the defaults match it). The
PostgreSQL counterpart is ``postgres/test_kh_parent_node.py``.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import FileRecord, RecordGroup, RecordGroupType, RecordType
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("kh-parent-node-it")

FOLDER_MIME_TYPES = [MimeTypes.FOLDER.value]


@dataclass
class _Tree:
    graph: object
    processor: DataSourceEntitiesProcessor
    org_id: str
    connector_id: str
    now: int

    def group(self, name: str, *, group_id: str | None = None) -> RecordGroup:
        return RecordGroup(
            **({"id": group_id} if group_id else {}),
            org_id=self.org_id,
            name=name,
            external_group_id=f"{name}-{self.connector_id}",
            connector_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
            connector_id=self.connector_id,
            group_type=RecordGroupType.DRIVE,
            source_created_at=self.now,
            source_updated_at=self.now,
        )

    def record(
        self, name: str, *, group: str | None, parent: str | None = None, folder: bool = False, shared: tuple[str, ...] = (),
    ) -> FileRecord:
        return FileRecord(
            id=str(uuid.uuid4()),
            org_id=self.org_id,
            record_name=name,
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE,
            external_record_id=f"{name}-{self.connector_id}",
            external_record_group_id=f"{group}-{self.connector_id}" if group else None,
            external_revision_id="rev-1",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
            connector_id=self.connector_id,
            created_at=self.now,
            updated_at=self.now,
            source_created_at=self.now,
            source_updated_at=self.now,
            is_file=not folder,
            mime_type=MimeTypes.FOLDER.value if folder else MimeTypes.PDF.value,
            parent_external_record_id=f"{parent}-{self.connector_id}" if parent else None,
            parent_record_type=RecordType.FILE if parent else None,
            indexing_status=ProgressStatus.COMPLETED.value,
            shared_with_me_record_group_ids=[f"{g}-{self.connector_id}" for g in shared],
        )

    async def record_id(self, name: str) -> str:
        stored = await self.graph.get_record_by_external_id(self.connector_id, f"{name}-{self.connector_id}")
        assert stored is not None, name
        return stored.id

    async def group_id(self, name: str) -> str:
        stored = await self.graph.get_record_group_by_external_id(self.connector_id, f"{name}-{self.connector_id}")
        assert stored is not None, name
        return stored.id

    async def parent_of(self, node_id: str) -> dict | None:
        return await self.graph.get_knowledge_hub_parent_node(node_id, FOLDER_MIME_TYPES)


async def _seed(graph: object) -> _Tree:
    run = uuid.uuid4().hex[:10]
    now = get_epoch_timestamp_in_ms()
    org_id, connector_id = f"org-{run}", f"drive-{run}"
    assert await graph.batch_upsert_nodes(
        [{"id": connector_id, "orgId": org_id, "name": "Drive", "type": Connectors.GOOGLE_DRIVE_WORKSPACE.value,
          "appGroup": "Google Workspace", "scope": "team", "isActive": True,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )
    processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
    processor.org_id = org_id
    producer = MagicMock()
    producer.send_messages = AsyncMock(side_effect=lambda topic, messages: [True] * len(messages))
    processor.messaging_producer = producer
    return _Tree(graph, processor, org_id, connector_id, now)


async def _cleanup(tree: _Tree) -> None:
    with contextlib.suppress(Exception):
        records = await tree.graph.get_nodes_by_filters(
            CollectionNames.RECORDS.value, {"connectorId": tree.connector_id}
        ) or []
        groups = await tree.graph.get_nodes_by_filters(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": tree.connector_id}
        ) or []
        await tree.graph.delete_nodes_and_edges(
            [r.get("_key") or r.get("id") for r in records], CollectionNames.RECORDS.value
        )
        await tree.graph.delete_nodes_and_edges(
            [g.get("_key") or g.get("id") for g in groups], CollectionNames.RECORD_GROUPS.value
        )
        await tree.graph.delete_nodes_and_edges([tree.connector_id], CollectionNames.APPS.value)


@pytest.fixture(params=["neo4j", "arango"])
async def tree(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Tree]:
    try:
        graph = await (
            connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
            else connect_arango(logger, "kh_parent_node_it")
        )
    except Exception as exc:
        backend_unavailable(request.param, exc)
    seeded = await _seed(graph)
    try:
        # drive "drive" > folder "docs" > file "guide" > file "figure"; "loose" sits directly in the drive.
        await seeded.processor.on_new_record_groups([(seeded.group("drive"), [])])
        await seeded.processor.on_new_records([
            (seeded.record("docs", group="drive", folder=True), []),
            (seeded.record("guide", group="drive", parent="docs"), []),
            (seeded.record("figure", group="drive", parent="guide"), []),
            (seeded.record("loose", group="drive"), []),
        ])
        yield seeded
    finally:
        await _cleanup(seeded)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            await disconnect()


async def test_a_record_under_a_folder_has_the_folder_as_its_parent(tree: _Tree) -> None:
    parent = await tree.parent_of(await tree.record_id("guide"))
    assert parent == {"id": await tree.record_id("docs"), "name": "docs", "nodeType": "folder", "subType": "FILE"}


async def test_a_record_under_a_record_that_is_not_a_folder_has_it_as_a_record_parent(tree: _Tree) -> None:
    parent = await tree.parent_of(await tree.record_id("figure"))
    assert parent == {"id": await tree.record_id("guide"), "name": "guide", "nodeType": "record", "subType": "FILE"}


async def test_a_record_directly_in_a_group_has_the_group_as_its_parent(tree: _Tree) -> None:
    parent = await tree.parent_of(await tree.record_id("loose"))
    assert parent == {"id": await tree.group_id("drive"), "name": "drive", "nodeType": "recordGroup", "subType": "DRIVE"}


async def test_a_group_has_its_app_as_parent_and_the_app_has_none(tree: _Tree) -> None:
    assert await tree.parent_of(await tree.group_id("drive")) == {
        "id": tree.connector_id, "name": "Drive", "nodeType": "app", "subType": Connectors.GOOGLE_DRIVE_WORKSPACE.value,
    }
    assert await tree.parent_of(tree.connector_id) is None


def _allow_every_node(tree: _Tree, monkeypatch: pytest.MonkeyPatch, *, hidden: frozenset[str] = frozenset()) -> None:
    """The breadcrumbs drop what the user cannot see; these tests are about the walk, not access, so every node but ``hidden`` is visible."""
    async def check_access(user_key: str, org_id: str, *, node_ids=(), **_: object) -> AccessCheck:
        return AccessCheck(node_ids=frozenset(node_ids) - hidden)
    monkeypatch.setattr(tree.graph, "check_access", check_access)


async def _breadcrumb_ids(tree: _Tree, node_id: str) -> list[str]:
    return [step["id"] for step in await tree.graph.get_knowledge_hub_breadcrumbs(node_id, "user-1", tree.org_id)]


async def _assert_trail(tree: _Tree, monkeypatch: pytest.MonkeyPatch, record_id: str, *above: str) -> None:
    """The breadcrumbs reach the record from the app through ``above``, the last of them its parent node."""
    _allow_every_node(tree, monkeypatch)
    trail = await _breadcrumb_ids(tree, record_id)
    assert trail == [tree.connector_id, *above, record_id]
    assert trail[-2] == (await tree.parent_of(record_id))["id"]


def _ordered_ids() -> tuple[str, str]:
    """Two group ids, the first sorting before the second."""
    rest = str(uuid.uuid4())[1:]
    return f"0{rest}", f"f{rest}"


async def _groups_with_ids(tree: _Tree, *groups: RecordGroup) -> None:
    """Stored as given, each under the app: the processor would give each group a fresh id, and these tests need their order."""
    assert await tree.graph.batch_upsert_record_groups(list(groups)) is not False
    await tree.graph.batch_create_edges(
        [{"from_id": group.id, "from_collection": CollectionNames.RECORD_GROUPS.value, "to_id": tree.connector_id,
          "to_collection": CollectionNames.APPS.value} for group in groups],
        collection=CollectionNames.BELONGS_TO.value,
    )


async def _membership(tree: _Tree, record: str, group: str, *, hierarchy: bool) -> None:
    """A BELONGS_TO edge from the record to the group, and the group's hierarchy edge to it when ``hierarchy``."""
    record_id, group_id = await tree.record_id(record), await tree.group_id(group)
    records, groups = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value
    await tree.graph.create_record_group_relation(record_id, group_id)
    if hierarchy:
        await tree.graph.batch_create_edges(
            [{"from_id": group_id, "from_collection": groups, "to_id": record_id, "to_collection": records,
              "relationshipType": "PARENT_CHILD"}],
            collection=CollectionNames.NODE_RELATIONS.value,
        )


async def _shared_top_level_records(tree: _Tree, home_id: str, shared_id: str, names: list[str], *, home_edge_rewritten: bool) -> list[str]:
    """Top-level records of group "home" shared into "0S:someone", as the processor writes them (each group a BELONGS_TO and a hierarchy edge)."""
    await _groups_with_ids(tree, tree.group("home", group_id=home_id), tree.group("0S:someone", group_id=shared_id))
    await tree.processor.on_new_records([(tree.record(name, group="home", shared=("0S:someone",)), []) for name in names])
    record_ids = [await tree.record_id(name) for name in names]
    if home_edge_rewritten:
        for record_id in record_ids:
            await tree.graph.delete_edge(record_id, CollectionNames.RECORDS.value, home_id,
                                         CollectionNames.RECORD_GROUPS.value, CollectionNames.BELONGS_TO.value)
            await tree.graph.create_record_group_relation(record_id, home_id)
    return record_ids


@pytest.mark.parametrize("home_edge_rewritten", [False, True], ids=["as-written", "home-edge-rewritten"])
@pytest.mark.parametrize("home_sorts_first", [True, False], ids=["home-id-first", "shared-id-first"])
async def test_a_top_level_record_shared_into_a_second_group_has_its_own_group_as_parent(
    tree: _Tree, home_sorts_first: bool, home_edge_rewritten: bool,
) -> None:
    low, high = _ordered_ids()
    home_id, shared_id = (low, high) if home_sorts_first else (high, low)
    record_ids = await _shared_top_level_records(tree, home_id, shared_id, [f"report-{n}" for n in range(6)],
                                                 home_edge_rewritten=home_edge_rewritten)

    parents = {record_id: await tree.parent_of(record_id) for record_id in record_ids}

    home = {"id": home_id, "name": "home", "nodeType": "recordGroup", "subType": "DRIVE"}
    assert {record_id: parent for record_id, parent in parents.items() if parent != home} == {}


async def test_a_record_moved_to_another_group_after_it_was_shared_has_its_new_group_as_parent(tree: _Tree) -> None:
    await tree.processor.on_new_record_groups([
        (tree.group("old-home"), []), (tree.group("0S:someone"), []), (tree.group("new-home"), []),
    ])
    await tree.processor.on_new_records([(tree.record("report", group="old-home", shared=("0S:someone",)), [])])
    await tree.processor.on_new_records([(tree.record("report", group="new-home", shared=("0S:someone",)), [])])

    parent = await tree.parent_of(await tree.record_id("report"))

    assert parent == {"id": await tree.group_id("new-home"), "name": "new-home", "nodeType": "recordGroup",
                      "subType": "DRIVE"}


async def test_a_record_in_a_group_without_a_hierarchy_edge_has_the_group_that_has_one_as_parent(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch,
) -> None:
    low, high = _ordered_ids()
    await _groups_with_ids(tree, tree.group("member-only", group_id=low), tree.group("listed", group_id=high))
    await tree.processor.on_new_records([(tree.record("report", group=None), [])])
    await _membership(tree, "report", "member-only", hierarchy=False)
    await _membership(tree, "report", "listed", hierarchy=True)

    parent = await tree.parent_of(await tree.record_id("report"))

    assert parent == {"id": high, "name": "listed", "nodeType": "recordGroup", "subType": "DRIVE"}
    await _assert_trail(tree, monkeypatch, await tree.record_id("report"), high)


async def test_a_record_under_a_folder_and_shared_into_a_group_has_the_folder_as_parent(tree: _Tree) -> None:
    await tree.processor.on_new_record_groups([(tree.group("0S:someone"), [])])
    await tree.processor.on_new_records([(tree.record("shared-guide", group="drive", parent="docs", shared=("0S:someone",)), [])])

    parent = await tree.parent_of(await tree.record_id("shared-guide"))

    assert parent == {"id": await tree.record_id("docs"), "name": "docs", "nodeType": "folder", "subType": "FILE"}


async def test_a_record_with_only_a_membership_has_that_group_as_parent(tree: _Tree) -> None:
    await tree.processor.on_new_record_groups([(tree.group("member-only"), [])])
    await tree.processor.on_new_records([(tree.record("report", group=None), [])])
    await _membership(tree, "report", "member-only", hierarchy=False)

    parent = await tree.parent_of(await tree.record_id("report"))

    assert parent == {"id": await tree.group_id("member-only"), "name": "member-only", "nodeType": "recordGroup",
                      "subType": "DRIVE"}


def _group_parent(group_id: str, name: str) -> dict:
    return {"id": group_id, "name": name, "nodeType": "recordGroup", "subType": "DRIVE"}


async def _drop_hierarchy_edge(tree: _Tree, record: str, group: str) -> None:
    """What a record written before the hierarchy edge existed lacks."""
    await tree.graph.delete_edge(await tree.group_id(group), CollectionNames.RECORD_GROUPS.value, await tree.record_id(record),
                                 CollectionNames.RECORDS.value, CollectionNames.NODE_RELATIONS.value)


@pytest.mark.parametrize("home_sorts_first", [True, False], ids=["home-id-first", "listed-id-first"])
async def test_the_records_own_group_wins_over_a_group_with_the_hierarchy_edge(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, home_sorts_first: bool,
) -> None:
    low, high = _ordered_ids()
    home_id, listed_id = (low, high) if home_sorts_first else (high, low)
    await _groups_with_ids(tree, tree.group("home", group_id=home_id), tree.group("listed", group_id=listed_id))
    await tree.processor.on_new_records([(tree.record("report", group="home"), [])])
    await _drop_hierarchy_edge(tree, "report", "home")
    await _membership(tree, "report", "listed", hierarchy=True)

    assert await tree.parent_of(await tree.record_id("report")) == _group_parent(home_id, "home")
    await _assert_trail(tree, monkeypatch, await tree.record_id("report"), home_id)


async def test_the_records_own_group_wins_over_a_lower_id_record_it_belongs_to(tree: _Tree) -> None:
    low, high = _ordered_ids()
    await _groups_with_ids(tree, tree.group("home", group_id=high))
    project = tree.record("project", group="drive", folder=True)
    project.id = low
    await tree.processor.on_new_records([(project, [])])
    await tree.processor.on_new_records([(tree.record("report", group="home"), [])])
    await _drop_hierarchy_edge(tree, "report", "home")
    await tree.graph.batch_create_edges(
        [{"from_id": await tree.record_id("report"), "from_collection": CollectionNames.RECORDS.value, "to_id": low,
          "to_collection": CollectionNames.RECORDS.value}],
        collection=CollectionNames.BELONGS_TO.value,
    )

    assert await tree.parent_of(await tree.record_id("report")) == _group_parent(high, "home")


@pytest.mark.parametrize("low_written_first", [True, False], ids=["low-written-first", "high-written-first"])
async def test_a_record_whose_own_group_it_no_longer_belongs_to_has_the_lowest_listed_group(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, low_written_first: bool,
) -> None:
    low, high = _ordered_ids()
    await _groups_with_ids(tree, tree.group("left", group_id=f"5{low[1:]}"), tree.group("low", group_id=low),
                           tree.group("high", group_id=high))
    await tree.processor.on_new_records([(tree.record("report", group="left"), [])])
    await tree.graph.delete_edge(await tree.record_id("report"), CollectionNames.RECORDS.value, f"5{low[1:]}",
                                 CollectionNames.RECORD_GROUPS.value, CollectionNames.BELONGS_TO.value)
    for name in (("low", "high") if low_written_first else ("high", "low")):
        await _membership(tree, "report", name, hierarchy=True)

    assert await tree.parent_of(await tree.record_id("report")) == _group_parent(low, "low")
    await _assert_trail(tree, monkeypatch, await tree.record_id("report"), low)


@pytest.mark.parametrize("low_written_first", [True, False], ids=["low-written-first", "high-written-first"])
@pytest.mark.parametrize("own_group", [True, False], ids=["own-group-high", "no-own-group"])
async def test_a_record_in_several_groups_without_any_hierarchy_edge_has_its_own_group_else_the_lowest(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, own_group: bool, low_written_first: bool,
) -> None:
    low, high = _ordered_ids()
    await _groups_with_ids(tree, tree.group("low", group_id=low), tree.group("high", group_id=high))
    await tree.processor.on_new_records([(tree.record("report", group="high" if own_group else None), [])])
    if own_group:
        await _drop_hierarchy_edge(tree, "report", "high")
    for name in (("low", "high") if low_written_first else ("high", "low")):
        await _membership(tree, "report", name, hierarchy=False)

    expected = _group_parent(high, "high") if own_group else _group_parent(low, "low")
    assert await tree.parent_of(await tree.record_id("report")) == expected
    await _assert_trail(tree, monkeypatch, await tree.record_id("report"), expected["id"])


async def test_a_group_hierarchy_edge_without_membership_is_not_a_parent(tree: _Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    low, high = _ordered_ids()
    await _groups_with_ids(tree, tree.group("ghost", group_id=low), tree.group("home", group_id=high))
    await tree.processor.on_new_records([(tree.record("report", group=None), [])])
    await _membership(tree, "report", "home", hierarchy=False)
    await tree.graph.batch_create_edges(
        [{"from_id": low, "from_collection": CollectionNames.RECORD_GROUPS.value, "to_id": await tree.record_id("report"),
          "to_collection": CollectionNames.RECORDS.value, "relationshipType": "PARENT_CHILD"}],
        collection=CollectionNames.NODE_RELATIONS.value,
    )

    assert await tree.parent_of(await tree.record_id("report")) == _group_parent(high, "home")
    await _assert_trail(tree, monkeypatch, await tree.record_id("report"), high)


async def test_a_record_dropped_from_a_shared_group_has_its_own_group(tree: _Tree) -> None:
    low, high = _ordered_ids()
    await _groups_with_ids(tree, tree.group("0S:someone", group_id=low), tree.group("home", group_id=high))
    await tree.processor.on_new_records([(tree.record("report", group="home", shared=("0S:someone",)), [])])
    resynced = tree.record("report", group="home")
    resynced.shared_groups_complete = True
    await tree.processor.on_new_records([(resynced, [])])

    record_id = await tree.record_id("report")
    ends = {
        end
        for edges in (
            await tree.graph.get_edges_from_node(f"{CollectionNames.RECORDS.value}/{record_id}", CollectionNames.BELONGS_TO.value),
            await tree.graph.get_edges_from_node(f"{CollectionNames.RECORDS.value}/{record_id}", CollectionNames.INHERIT_PERMISSIONS.value),
            await tree.graph.get_edges_to_node(f"{CollectionNames.RECORDS.value}/{record_id}", CollectionNames.NODE_RELATIONS.value),
        )
        for edge in edges or []
        for end in (edge.get("_from"), edge.get("_to"))
    }
    assert f"{CollectionNames.RECORD_GROUPS.value}/{high}" in ends
    assert f"{CollectionNames.RECORD_GROUPS.value}/{low}" not in ends
    assert await tree.parent_of(record_id) == _group_parent(high, "home")


async def test_a_record_with_two_record_parents_has_the_lowest_id_parent(tree: _Tree, monkeypatch: pytest.MonkeyPatch) -> None:
    low, high = _ordered_ids()
    folders = [tree.record("folder-high", group="drive", folder=True), tree.record("folder-low", group="drive", folder=True)]
    folders[0].id, folders[1].id = high, low
    await tree.processor.on_new_records([(folder, []) for folder in folders])
    await tree.processor.on_new_records([(tree.record("report", group="drive", parent="folder-high"), [])])
    await tree.graph.batch_create_edges(
        [{"from_id": low, "from_collection": CollectionNames.RECORDS.value, "to_id": await tree.record_id("report"),
          "to_collection": CollectionNames.RECORDS.value, "relationshipType": "PARENT_CHILD"}],
        collection=CollectionNames.NODE_RELATIONS.value,
    )

    assert await tree.parent_of(await tree.record_id("report")) == {
        "id": low, "name": "folder-low", "nodeType": "folder", "subType": "FILE",
    }
    await _assert_trail(tree, monkeypatch, await tree.record_id("report"), await tree.group_id("drive"), low)


async def test_a_record_with_only_inherit_edges_to_several_groups_has_the_lowest(tree: _Tree) -> None:
    low, high = _ordered_ids()
    await _groups_with_ids(tree, tree.group("low", group_id=low), tree.group("high", group_id=high))
    await tree.processor.on_new_records([(tree.record("report", group=None), [])])
    record_id = await tree.record_id("report")
    for group_id in (high, low):
        await tree.graph.batch_create_edges(
            [{"from_id": record_id, "from_collection": CollectionNames.RECORDS.value, "to_id": group_id,
              "to_collection": CollectionNames.RECORD_GROUPS.value}],
            collection=CollectionNames.INHERIT_PERMISSIONS.value,
        )

    assert await tree.parent_of(record_id) == _group_parent(low, "low")


@pytest.mark.parametrize("home_edge_rewritten", [False, True], ids=["as-written", "home-edge-rewritten"])
@pytest.mark.parametrize("home_sorts_first", [True, False], ids=["home-id-first", "shared-id-first"])
async def test_the_breadcrumbs_of_a_shared_top_level_record_go_through_its_own_group(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, home_sorts_first: bool, home_edge_rewritten: bool,
) -> None:
    _allow_every_node(tree, monkeypatch)
    low, high = _ordered_ids()
    home_id, shared_id = (low, high) if home_sorts_first else (high, low)
    record_ids = await _shared_top_level_records(tree, home_id, shared_id, [f"report-{n}" for n in range(6)],
                                                 home_edge_rewritten=home_edge_rewritten)

    # Neo4j gave no order to the groups a record belongs to, so one record's answer could change from call to call.
    for record_id in record_ids:
        for _ in range(10):
            trail = await _breadcrumb_ids(tree, record_id)
            assert trail == [tree.connector_id, home_id, record_id]
            assert trail[-2] == (await tree.parent_of(record_id))["id"]


@pytest.mark.parametrize("home_sorts_first", [True, False], ids=["home-id-first", "shared-id-first"])
async def test_the_breadcrumbs_skip_a_shared_records_own_group_the_user_cannot_see(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch, home_sorts_first: bool,
) -> None:
    """The hidden group is left out of the trail, not replaced by the other group the record is shared into."""
    low, high = _ordered_ids()
    home_id, shared_id = (low, high) if home_sorts_first else (high, low)
    record_ids = await _shared_top_level_records(tree, home_id, shared_id, [f"report-{n}" for n in range(6)], home_edge_rewritten=False)
    _allow_every_node(tree, monkeypatch, hidden=frozenset({home_id}))

    trails = {record_id: await _breadcrumb_ids(tree, record_id) for record_id in record_ids}

    assert trails == {record_id: [tree.connector_id, record_id] for record_id in record_ids}


async def test_the_breadcrumbs_of_a_record_under_a_folder_shared_into_a_group_go_through_the_folder(
    tree: _Tree, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_every_node(tree, monkeypatch)
    await tree.processor.on_new_record_groups([(tree.group("0S:someone"), [])])
    await tree.processor.on_new_records([(tree.record("shared-guide", group="drive", parent="docs", shared=("0S:someone",)), [])])

    assert await _breadcrumb_ids(tree, await tree.record_id("shared-guide")) == [
        tree.connector_id, await tree.group_id("drive"), await tree.record_id("docs"), await tree.record_id("shared-guide"),
    ]
