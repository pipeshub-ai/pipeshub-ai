"""Knowledge Hub app browse for external collaborators, on a real Neo4j and a real ArangoDB.

Browse walks down from the app one level at a time, so a record or nested group
shared with an external collaborator (``userAppRelation.isExternalUser``, #3115)
whose container they cannot see would be unreachable. App browse hoists exactly
those to the app level: a node is hoisted when the user has a role on it and on
none of its immediate parents.

One seeded Drive app, browsed by three users:

* the external collaborator, who sees the one top-level group shared with them
  plus every orphan: a record and a folder in a hidden group, a record shared
  through a user group whose folder is hidden, and a nested group under a
  hidden group. Not hoisted: a record whose folder they can see, a nested group
  whose parent they can see, a record with no container, a deleted record.
* a colleague with the same grants but no external flag, who gets no hoisting.
* the owner, who sees both top-level groups.

Both backends must return the same nodes with the same type, role and children
flag. On Arango the test also asks the optimizer for the query's plan: nesting
the permission lookups once made planning alone need more than 1 GB.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "knowledge_hub_app_browse_it"

logger = logging.getLogger("knowledge-hub-app-browse-it")

GROUPS = ("hidden_top", "shared_top", "nested_orphan", "nested_visible")
RECORDS = (
    "orphan_direct", "hidden_folder", "in_hidden_folder", "shared_folder",
    "in_shared_folder", "in_shared_top", "parentless", "deleted",
)
FOLDERS = {"hidden_folder", "shared_folder"}
USERS = ("external", "guest", "owner")

# What the external collaborator and the unflagged guest are granted, directly.
DIRECT_GRANTS = (
    "shared_top", "nested_orphan", "nested_visible", "orphan_direct",
    "shared_folder", "in_shared_folder", "in_shared_top", "parentless", "deleted",
)

# (nodeType, userRole, hasChildren) by name.
EXTERNAL_SEES = {
    "shared_top": ("recordGroup", "READER", True),
    "nested_orphan": ("recordGroup", "READER", False),
    "orphan_direct": ("record", "READER", False),
    "in_hidden_folder": ("record", "READER", False),
    "shared_folder": ("folder", "READER", True),
}
GUEST_SEES = {"shared_top": ("recordGroup", "READER", True)}
OWNER_SEES = {
    "hidden_top": ("recordGroup", "OWNER", True),
    "shared_top": ("recordGroup", "OWNER", True),
}


@dataclass
class _World:
    graph: IGraphDBProvider
    run: str
    org_id: str
    app_id: str
    ids: dict[str, str] = field(default_factory=dict)

    @property
    def user_group_id(self) -> str:
        return f"ugroup-{self.run}"


async def _remove(w: _World) -> None:
    ids = [*w.ids.values(), w.app_id, w.user_group_id]
    if isinstance(w.graph, Neo4jProvider):
        await w.graph.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids OR n.connectorId = $app DETACH DELETE n",
            parameters={"ids": ids, "app": w.app_id},
        )
        return
    for collection in (
        CollectionNames.RECORDS.value,
        CollectionNames.RECORD_GROUPS.value,
        CollectionNames.FILES.value,
        CollectionNames.USERS.value,
        CollectionNames.APPS.value,
        CollectionNames.GROUPS.value,
    ):
        await w.graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (
        CollectionNames.PERMISSION.value,
        CollectionNames.BELONGS_TO.value,
        CollectionNames.IS_OF_TYPE.value,
        CollectionNames.USER_APP_RELATION.value,
        CollectionNames.RECORD_RELATIONS.value,
    ):
        await w.graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


def _record(w: _World, name: str) -> FileRecord:
    now = get_epoch_timestamp_in_ms()
    folder = name in FOLDERS
    return FileRecord(
        id=w.ids[name],
        org_id=w.org_id,
        record_name=name,
        record_type=RecordType.FILE,
        external_record_id=f"ext-{w.ids[name]}",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE,
        connector_id=w.app_id,
        mime_type="application/vnd.folder" if folder else "application/pdf",
        indexing_status=ProgressStatus.COMPLETED.value,
        is_file=not folder,
        extension=None if folder else "pdf",
        size_in_bytes=0 if folder else 1024,
        created_at=now,
        updated_at=now,
        source_created_at=now,
        source_updated_at=now,
    )


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    stamps = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    for name in (*GROUPS, *RECORDS, *USERS):
        w.ids[name] = f"{name}-{w.run}"

    assert await g.batch_upsert_nodes(
        [{"id": w.ids[u], "userId": f"uid-{w.ids[u]}", "orgId": w.org_id,
          "email": f"{w.ids[u]}@example.com", "fullName": u, "isActive": True, **stamps}
         for u in USERS],
        collection=CollectionNames.USERS.value,
    )
    assert await g.batch_upsert_nodes(
        [{"id": w.app_id, "name": "Google Drive", "type": Connectors.GOOGLE_DRIVE.value,
          "appGroup": "Google Workspace", "scope": "team", "isActive": True, **stamps}],
        collection=CollectionNames.APPS.value,
    )
    assert await g.batch_upsert_nodes(
        [{"id": w.ids[name], "orgId": w.org_id, "groupName": name, "externalGroupId": f"ext-{w.ids[name]}",
          "groupType": "DRIVE", "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": w.app_id,
          "sourceCreatedAtTimestamp": now, "sourceLastModifiedTimestamp": now, **stamps}
         for name in GROUPS],
        collection=CollectionNames.RECORD_GROUPS.value,
    )
    assert await g.batch_upsert_nodes(
        [{"id": w.user_group_id, "orgId": w.org_id, "name": "Partners",
          "externalGroupId": f"ext-{w.user_group_id}", "connectorName": Connectors.GOOGLE_DRIVE.value,
          "connectorId": w.app_id, **stamps}],
        collection=CollectionNames.GROUPS.value,
    )
    await g.batch_upsert_records([_record(w, name) for name in RECORDS])
    await g.update_node(w.ids["deleted"], CollectionNames.RECORDS.value, {"isDeleted": True})

    def edge(src: str, src_coll: str, dst: str, dst_coll: str, **extra: object) -> dict:
        return {"from_id": src, "from_collection": src_coll, "to_id": dst, "to_collection": dst_coll,
                **stamps, **extra}

    rg, rec, usr = (CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value,
                    CollectionNames.USERS.value)
    belongs = [
        edge(w.ids["hidden_top"], rg, w.app_id, CollectionNames.APPS.value),
        edge(w.ids["shared_top"], rg, w.app_id, CollectionNames.APPS.value),
        edge(w.ids["nested_orphan"], rg, w.ids["hidden_top"], rg),
        edge(w.ids["nested_visible"], rg, w.ids["shared_top"], rg),
        *(edge(w.ids[name], rec, w.ids["hidden_top"], rg) for name in (
            "orphan_direct", "hidden_folder", "in_hidden_folder", "shared_folder",
            "in_shared_folder", "deleted",
        )),
        edge(w.ids["in_shared_top"], rec, w.ids["shared_top"], rg),
    ]
    assert await g.batch_create_edges(belongs, collection=CollectionNames.BELONGS_TO.value)
    assert await g.batch_create_edges(
        [edge(w.ids[parent], rec, w.ids[child], rec, relationshipType="PARENT_CHILD")
         for parent, child in (("hidden_folder", "in_hidden_folder"), ("shared_folder", "in_shared_folder"))],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )

    grants = [
        edge(w.ids[user], usr, w.ids[name], rg if name in GROUPS else rec, role="READER", type="USER")
        for user in ("external", "guest") for name in DIRECT_GRANTS
    ]
    grants += [
        edge(w.ids["owner"], usr, w.ids[name], rg, role="OWNER", type="USER")
        for name in ("hidden_top", "shared_top")
    ]
    # Shared with the external collaborator only through a user group.
    grants += [
        edge(w.ids["external"], usr, w.user_group_id, CollectionNames.GROUPS.value, role="READER", type="USER"),
        edge(w.user_group_id, CollectionNames.GROUPS.value, w.ids["in_hidden_folder"], rec,
             role="READER", type="GROUP"),
    ]
    assert await g.batch_create_edges(grants, collection=CollectionNames.PERMISSION.value)
    assert await g.batch_create_edges(
        [edge(w.ids[user], usr, w.app_id, CollectionNames.APPS.value, syncState="COMPLETED",
              lastSyncUpdate=now, isExternalUser=user == "external")
         for user in USERS],
        collection=CollectionNames.USER_APP_RELATION.value,
    )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        w = _World(graph=graph, run=run, org_id=f"org-khb-{run}", app_id=f"drive-khb-{run}")
        cleanup.push_async_callback(_remove, w)
        await _seed(w)
        yield w


async def _browse(w: _World, user: str) -> dict[str, tuple]:
    got = await w.graph.get_knowledge_hub_children(
        w.app_id, "app", w.org_id, w.ids[user], 0, 100, "name", "ASC",
    )
    names = {v: k for k, v in w.ids.items()}
    nodes = got["nodes"]
    assert got["total"] == len(nodes), got
    for node in nodes:
        assert node["parentId"] == f"apps/{w.app_id}", node
        assert node["origin"] == "CONNECTOR", node
    assert len({n["id"] for n in nodes}) == len(nodes), f"a node is listed twice: {nodes}"
    return {names.get(n["id"], n["id"]): (n["nodeType"], n["userRole"], n["hasChildren"]) for n in nodes}


@pytest.mark.parametrize(("user", "expected"), [
    ("external", EXTERNAL_SEES),
    ("guest", GUEST_SEES),
    ("owner", OWNER_SEES),
])
async def test_app_browse_hoists_only_what_an_external_collaborator_cannot_reach(
    world: _World, user: str, expected: dict[str, tuple],
) -> None:
    assert await _browse(world, user) == expected


async def test_app_browse_pages_and_counts_the_hoisted_nodes(world: _World) -> None:
    first = await world.graph.get_knowledge_hub_children(
        world.app_id, "app", world.org_id, world.ids["external"], 0, 2, "name", "ASC",
    )
    rest = await world.graph.get_knowledge_hub_children(
        world.app_id, "app", world.org_id, world.ids["external"], 2, 100, "name", "ASC",
    )
    assert first["total"] == rest["total"] == len(EXTERNAL_SEES)
    names = [n["name"] for n in first["nodes"] + rest["nodes"]]
    assert names == sorted(EXTERNAL_SEES), names


async def test_app_browse_plans_in_bounded_memory(world: _World) -> None:
    """Arango: one plan, small planner footprint. Neo4j: the browse simply answers."""
    graph = world.graph
    if not isinstance(graph, ArangoHTTPProvider):
        assert await _browse(world, "external") == EXTERNAL_SEES
        return

    client = graph.http_client
    sent: list[dict] = []
    execute = client.execute_aql

    async def capture(query: str, bind_vars: dict | None = None, txn_id: str | None = None, **kwargs: object) -> list[dict]:
        sent.append({"query": query, "bindVars": bind_vars or {}, "options": kwargs.get("options") or {}})
        return await execute(query, bind_vars=bind_vars, txn_id=txn_id, **kwargs)

    client.execute_aql = capture
    try:
        assert await _browse(world, "external") == EXTERNAL_SEES
    finally:
        client.execute_aql = execute
    assert len(sent) == 1, sent

    session = await client._get_session()
    async with session.post(
        f"{client.base_url}/_db/{client.database}/_api/explain",
        json={"query": sent[0]["query"], "bindVars": sent[0]["bindVars"], "options": sent[0]["options"]},
    ) as resp:
        assert resp.status == 200, await resp.text()
        stats = (await resp.json())["stats"]
    assert stats["plansCreated"] == 1, stats
    assert stats["peakMemoryUsage"] < 64 * 1024 * 1024, stats



@pytest.mark.parametrize("world", ["arango"], indirect=True)
async def test_app_browse_takes_a_hoisted_files_size_from_its_file_node(world: _World) -> None:
    """Older file docs carry ``sizeInBytes`` (deprecated there now); a record without its own size falls back to it."""
    await world.graph.update_node(
        world.ids["orphan_direct"], CollectionNames.RECORDS.value, {"sizeInBytes": None},
    )
    await world.graph.update_node(
        world.ids["orphan_direct"], CollectionNames.FILES.value, {"sizeInBytes": 4096},
    )
    got = await world.graph.get_knowledge_hub_children(
        world.app_id, "app", world.org_id, world.ids["external"], 0, 100, "name", "ASC",
    )
    sizes = {n["id"]: n.get("sizeInBytes") for n in got["nodes"]}
    assert sizes[world.ids["orphan_direct"]] == 4096, got["nodes"]
