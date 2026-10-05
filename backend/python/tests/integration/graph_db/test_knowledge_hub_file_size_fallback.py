"""Knowledge Hub listings fall back to the file node's ``sizeInBytes``, on a real Neo4j and a real ArangoDB.

Records store their size in ``sizeInBytes``. Older file documents carry it on the
file node instead (deprecated there now), which ``FileRecord.from_arango_record``
already reads as its fallback. The browse and search queries read
``fileSizeInBytes`` for that fallback, a field nothing writes, so such a file was
listed with no size. The record's own size still wins when it has one.

Seeded: a Drive app with a record group (a folder, a file inside it, a file with
only a file-node size and a file with both sizes), an internal record group, a
hidden record group holding a file shared with an external collaborator, and a
collection (KB) with one root file. Every "legacy" file has no size on its
record and 4096 on its file node.

Runs in backend-matrix on both graph jobs; the cases whose old query on one
backend already read the right field run on the other backend only. Environment:
NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
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

ARANGO_DB = "knowledge_hub_file_size_it"
LEGACY_SIZE = 4096
OWN_SIZE = 100

logger = logging.getLogger("knowledge-hub-file-size-it")

GROUPS = ("group", "internal_group", "hidden_group")
# name -> (container, parent folder, own size, file-node size)
RECORDS = {
    "folder": ("group", None, None, None),
    "in_folder": ("group", "folder", None, LEGACY_SIZE),
    "legacy": ("group", None, None, LEGACY_SIZE),
    "own_size": ("group", None, OWN_SIZE, 9999),
    "internal_legacy": ("internal_group", None, None, LEGACY_SIZE),
    "hoisted_legacy": ("hidden_group", None, None, LEGACY_SIZE),
    "kb_legacy": ("kb", None, None, LEGACY_SIZE),
}
USERS = ("owner", "external")


@dataclass
class _World:
    graph: IGraphDBProvider
    run: str
    org_id: str
    app_id: str
    kb_id: str
    ids: dict[str, str] = field(default_factory=dict)

    def name_of(self, node_id: str) -> str:
        return {v: k for k, v in self.ids.items()}.get(node_id, node_id)


async def _remove(w: _World) -> None:
    ids = [*w.ids.values(), w.app_id, w.kb_id]
    if isinstance(w.graph, Neo4jProvider):
        await w.graph.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids},
        )
        return
    for collection in (
        CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value, CollectionNames.FILES.value,
        CollectionNames.USERS.value, CollectionNames.APPS.value,
    ):
        await w.graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (
        CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value, CollectionNames.IS_OF_TYPE.value,
        CollectionNames.USER_APP_RELATION.value, CollectionNames.RECORD_RELATIONS.value,
    ):
        await w.graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


def _record(w: _World, name: str) -> FileRecord:
    container, parent, own_size, _ = RECORDS[name]
    now = get_epoch_timestamp_in_ms()
    is_folder = name == "folder"
    in_kb = container == "kb"
    return FileRecord(
        id=w.ids[name],
        org_id=w.org_id,
        record_name=name,
        record_type=RecordType.FILE,
        external_record_id=f"ext-{w.ids[name]}",
        parent_external_record_id=f"ext-{w.ids[parent]}" if parent else None,
        version=1,
        origin=OriginTypes.UPLOAD if in_kb else OriginTypes.CONNECTOR,
        connector_name=Connectors.KNOWLEDGE_BASE if in_kb else Connectors.GOOGLE_DRIVE,
        connector_id=w.kb_id if in_kb else w.app_id,
        mime_type="application/vnd.folder" if is_folder else "application/pdf",
        indexing_status=ProgressStatus.COMPLETED.value,
        is_file=not is_folder,
        extension=None if is_folder else "pdf",
        size_in_bytes=own_size,
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
    rg, rec, usr, apps = (CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value,
                          CollectionNames.USERS.value, CollectionNames.APPS.value)

    assert await g.batch_upsert_nodes(
        [{"id": w.ids[u], "userId": f"uid-{w.ids[u]}", "orgId": w.org_id,
          "email": f"{w.ids[u]}@example.com", "fullName": u, "isActive": True, **stamps}
         for u in USERS],
        collection=usr,
    )
    assert await g.batch_upsert_nodes(
        [{"id": w.app_id, "name": "Google Drive", "type": Connectors.GOOGLE_DRIVE.value,
          "appGroup": "Google Workspace", "scope": "team", "isActive": True, **stamps},
         {"id": w.kb_id, "name": "Collection", "type": Connectors.KNOWLEDGE_BASE.value,
          "appGroup": "Local Storage", "scope": "personal", "isActive": True, "orgId": w.org_id, **stamps}],
        collection=apps,
    )
    assert await g.batch_upsert_nodes(
        [{"id": w.ids[name], "orgId": w.org_id, "groupName": name, "externalGroupId": f"ext-{w.ids[name]}",
          "groupType": "DRIVE", "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": w.app_id,
          "isInternal": name == "internal_group",
          "sourceCreatedAtTimestamp": now, "sourceLastModifiedTimestamp": now, **stamps}
         for name in GROUPS],
        collection=rg,
    )
    await g.batch_upsert_records([_record(w, name) for name in RECORDS])
    for name, (_, _, _, file_size) in RECORDS.items():
        if file_size is not None:
            assert await g.update_node(w.ids[name], CollectionNames.FILES.value, {"sizeInBytes": file_size})

    def edge(src: str, src_coll: str, dst: str, dst_coll: str, **extra: object) -> dict:
        return {"from_id": src, "from_collection": src_coll, "to_id": dst, "to_collection": dst_coll,
                **stamps, **extra}

    belongs = [edge(w.ids[name], rg, w.app_id, apps) for name in GROUPS]
    for name, (container, _, _, _) in RECORDS.items():
        belongs.append(
            edge(w.ids[name], rec, w.kb_id, apps) if container == "kb"
            else edge(w.ids[name], rec, w.ids[container], rg)
        )
    assert await g.batch_create_edges(belongs, collection=CollectionNames.BELONGS_TO.value)
    assert await g.batch_create_edges(
        [edge(w.ids["folder"], rec, w.ids["in_folder"], rec, relationshipType="PARENT_CHILD")],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )

    owner_sees = [n for n in RECORDS if RECORDS[n][0] != "hidden_group"]
    grants = [edge(w.ids["owner"], usr, w.ids[n], rg, role="OWNER", type="USER")
              for n in ("group", "internal_group")]
    grants += [edge(w.ids["owner"], usr, w.ids[n], rec, role="OWNER", type="USER") for n in owner_sees]
    grants.append(edge(w.ids["external"], usr, w.ids["hoisted_legacy"], rec, role="READER", type="USER"))
    assert await g.batch_create_edges(grants, collection=CollectionNames.PERMISSION.value)
    assert await g.batch_create_edges(
        [edge(w.ids[u], usr, app, apps, syncState="COMPLETED", lastSyncUpdate=now,
              isExternalUser=u == "external")
         for u in USERS for app in (w.app_id, w.kb_id)],
        collection=CollectionNames.USER_APP_RELATION.value,
    )

    stored = await g.get_document(w.ids["legacy"], rec)
    assert stored is not None and stored.get("sizeInBytes") is None, (
        f"the legacy file was meant to have no size of its own: {stored}"
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
        w = _World(graph=graph, run=run, org_id=f"org-khsize-{run}", app_id=f"drive-khsize-{run}",
                   kb_id=f"kb-khsize-{run}")
        cleanup.push_async_callback(_remove, w)
        await _seed(w)
        yield w


async def _sizes(w: _World, parent: str, parent_type: str, user: str = "owner") -> dict[str, int | None]:
    got = await w.graph.get_knowledge_hub_children(
        parent, parent_type, w.org_id, w.ids[user], 0, 100, "name", "ASC",
    )
    return {w.name_of(n["id"]): n.get("sizeInBytes") for n in got["nodes"] if n["nodeType"] != "folder"}


async def test_a_record_group_lists_a_files_size_from_its_file_node(world: _World) -> None:
    assert await _sizes(world, world.ids["group"], "recordGroup") == {
        "legacy": LEGACY_SIZE, "own_size": OWN_SIZE,
    }


async def test_an_internal_record_group_lists_a_files_size_from_its_file_node(world: _World) -> None:
    assert await _sizes(world, world.ids["internal_group"], "recordGroup") == {"internal_legacy": LEGACY_SIZE}


async def test_a_folder_lists_a_files_size_from_its_file_node(world: _World) -> None:
    assert await _sizes(world, world.ids["folder"], "folder") == {"in_folder": LEGACY_SIZE}


# ArangoDB's collection root and hoist already read sizeInBytes (the hoist is
# #3795's); only Neo4j's read the unwritten field.
@pytest.mark.parametrize("world", ["neo4j"], indirect=True)
async def test_a_collection_root_lists_a_files_size_from_its_file_node(world: _World) -> None:
    assert await _sizes(world, world.kb_id, "app") == {"kb_legacy": LEGACY_SIZE}


@pytest.mark.parametrize("world", ["neo4j"], indirect=True)
async def test_a_file_hoisted_for_an_external_collaborator_keeps_its_file_node_size(world: _World) -> None:
    assert await _sizes(world, world.app_id, "app", user="external") == {"hoisted_legacy": LEGACY_SIZE}


# Neo4j's search already reads sizeInBytes.
@pytest.mark.parametrize("world", ["arango"], indirect=True)
async def test_search_sorts_and_reports_a_files_size_from_its_file_node(world: _World) -> None:
    """Phase 1 sorts on the minimal node's size; phase 2 hydrates the size that is shown."""
    got = await world.graph.get_knowledge_hub_search(
        world.org_id, world.ids["owner"], 0, 100, "sizeInBytes", "ASC",
        node_types=["record"], parent_id=world.ids["group"], parent_type="recordGroup",
    )
    listed = [(world.name_of(n["id"]), n.get("sizeInBytes")) for n in got["nodes"]]
    assert listed[:1] == [("own_size", OWN_SIZE)], f"a file without a size sorted first: {listed}"
    assert dict(listed) == {"own_size": OWN_SIZE, "legacy": LEGACY_SIZE, "in_folder": LEGACY_SIZE}, listed
