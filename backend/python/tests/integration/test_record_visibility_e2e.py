"""Records in the trash, against a real Neo4j and a real ArangoDB.

Seeds one org with live records and records marked deleted (``isDeleted`` plus
the soft-delete fields), side by side in the same connector, parent, knowledge
base, md5 group and virtual record id, with the same permission edges. Then
calls the provider methods listed in ``tests/support/record_visibility_registry.py``
and checks each answer against its class there:

- ``PARAM`` methods return live records by default, only trashed ones with
  ``DELETED`` and both with ``ALL``;
- ``LIVE`` methods never return a trashed record, and still return the live one
  next to it (so a query that returns nothing cannot pass);
- ``ALL`` methods still find the trashed record.

Arango enforces the records schema strictly, so the Arango run also proves the
new fields are declared: an undeclared one would reject the seed.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips, so the bare unit job can
collect this file:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_record_visibility_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DeleteSource,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "record_visibility_it"

logger = logging.getLogger("record-visibility-it")


@dataclass
class _World:
    graph: IGraphDBProvider
    org_id: str
    user_id: str
    user_key: str
    connector_id: str
    kb_id: str
    parent_ext: str
    md5_dup: str
    md5_queued_trash: str
    md5_queued_live: str
    shared_vrid: str
    record_group_id: str
    # name -> record id
    ids: dict[str, str] = field(default_factory=dict)
    vrids: dict[str, str] = field(default_factory=dict)

    def ext(self, name: str) -> str:
        return f"ext-{name}-{self.connector_id}"

    def url(self, name: str) -> str:
        return f"https://source.example/{self.connector_id}/{name}"


LIVE_CONNECTOR = ("live", "live_shared", "live_failed", "ref_trash_q", "ref_live_q", "queued_live")
TRASHED_CONNECTOR = ("trashed", "trashed_shared", "trashed_failed", "queued_trash")
KB_RECORDS = ("kb_live", "kb_trashed", "kb_folder", "kb_child_live", "kb_child_trashed")


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


async def _connect_neo4j(monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    return provider


async def _connect_arango() -> IGraphDBProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB}
    )
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("ArangoHTTPProvider.connect returned False")
    # Applies the strict collection schemas and the indexes, to existing collections too.
    await provider.ensure_schema()
    return provider


async def _remove(graph: IGraphDBProvider, world: _World) -> None:
    ids = [*world.ids.values(), world.user_key, world.connector_id, world.kb_id, world.record_group_id]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids}
        )
        return
    for collection in (
        CollectionNames.RECORDS.value,
        CollectionNames.RECORD_GROUPS.value,
        CollectionNames.FILES.value,
        CollectionNames.USERS.value,
        CollectionNames.APPS.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (
        CollectionNames.PERMISSION.value,
        CollectionNames.BELONGS_TO.value,
        CollectionNames.IS_OF_TYPE.value,
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.USER_APP_RELATION.value,
        CollectionNames.RECORD_RELATIONS.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (_connect_neo4j(monkeypatch) if request.param == "neo4j" else _connect_arango())
        except Exception as exc:
            env = "NEO4J_IT_URI" if request.param == "neo4j" else "ARANGO_IT_URL"
            if os.environ.get(env):
                pytest.fail(f"{request.param} is configured ({env}) but not reachable: {exc!r}")
            pytest.skip(f"{request.param} not configured ({env} unset) and not reachable locally: {exc!r}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        suffix = uuid.uuid4().hex[:10]
        w = _World(
            graph=graph,
            org_id=f"org-vis-{suffix}",
            user_id=f"user-vis-{suffix}",
            user_key=f"ukey-vis-{suffix}",
            connector_id=f"drive-vis-{suffix}",
            kb_id=f"kb-vis-{suffix}",
            parent_ext=f"folder-{suffix}",
            md5_dup=f"md5-dup-{suffix}",
            md5_queued_trash=f"md5-qt-{suffix}",
            md5_queued_live=f"md5-ql-{suffix}",
            shared_vrid=f"vrid-shared-{suffix}",
            record_group_id=f"rg-vis-{suffix}",
        )
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


# ---------------------------------------------------------------------------
# Seeding, through the provider API so both backends get the same shapes
# ---------------------------------------------------------------------------


def _file(w: _World, name: str, *, trashed: bool, kb: bool = False, **overrides: object) -> FileRecord:
    now = get_epoch_timestamp_in_ms()
    fields: dict = {
        "id": w.ids[name],
        "org_id": w.org_id,
        "record_name": f"{name}.pdf",
        "record_type": RecordType.FILE,
        "external_record_id": w.ext(name),
        "version": 1,
        "origin": OriginTypes.UPLOAD if kb else OriginTypes.CONNECTOR,
        "connector_name": Connectors.KNOWLEDGE_BASE if kb else Connectors.GOOGLE_DRIVE,
        "connector_id": w.kb_id if kb else w.connector_id,
        "mime_type": "application/pdf",
        "weburl": w.url(name),
        "indexing_status": ProgressStatus.COMPLETED.value,
        "is_file": True,
        "extension": "pdf",
        "parent_external_record_id": None if kb else w.parent_ext,
        "md5_hash": w.md5_dup,
        "size_in_bytes": 1024,
    }
    if trashed:
        fields.update(
            is_deleted=True,
            deleted_at=now - 1000,
            deleted_by_user_id=w.user_key,
            delete_source=DeleteSource.USER,
            delete_batch_id=f"batch-{w.org_id}",
        )
    fields.update(overrides)
    return FileRecord(**fields)


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    for name in (*LIVE_CONNECTOR, *TRASHED_CONNECTOR, *KB_RECORDS):
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
        w.vrids[name] = f"vrid-{w.ids[name]}"
    w.vrids["live_shared"] = w.vrids["trashed_shared"] = w.shared_vrid

    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": w.user_id, "orgId": w.org_id, "email": f"{w.user_id}@example.com",
          "fullName": "Visibility Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        [
            {"id": w.connector_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace",
             "scope": "team", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now},
            {"id": w.kb_id, "name": "Collection", "type": "KB", "appGroup": "Local Storage",
             "scope": "personal", "isActive": True, "orgId": w.org_id,
             "createdAtTimestamp": now, "updatedAtTimestamp": now},
        ],
        collection=CollectionNames.APPS.value,
    )

    status = ProgressStatus
    records = [
        _file(w, "live", trashed=False),
        _file(w, "trashed", trashed=True),
        _file(w, "live_shared", trashed=False, md5_hash=None),
        _file(w, "trashed_shared", trashed=True, md5_hash=None),
        _file(w, "live_failed", trashed=False, indexing_status=status.FAILED.value, md5_hash=None),
        _file(w, "trashed_failed", trashed=True, indexing_status=status.FAILED.value, md5_hash=None),
        _file(w, "ref_trash_q", trashed=False, md5_hash=w.md5_queued_trash, parent_external_record_id=None),
        _file(w, "queued_trash", trashed=True, md5_hash=w.md5_queued_trash,
              indexing_status=status.QUEUED.value, parent_external_record_id=None),
        _file(w, "ref_live_q", trashed=False, md5_hash=w.md5_queued_live, parent_external_record_id=None),
        _file(w, "queued_live", trashed=False, md5_hash=w.md5_queued_live,
              indexing_status=status.QUEUED.value, parent_external_record_id=None),
        _file(w, "kb_live", trashed=False, kb=True, md5_hash=None),
        _file(w, "kb_trashed", trashed=True, kb=True, md5_hash=None),
        _file(w, "kb_folder", trashed=False, kb=True, md5_hash=None, is_file=False,
              mime_type="application/vnd.folder", extension=None),
        _file(w, "kb_child_live", trashed=False, kb=True, md5_hash=None),
        _file(w, "kb_child_trashed", trashed=True, kb=True, md5_hash=None),
    ]
    await g.batch_upsert_records(records)
    for name, vrid in w.vrids.items():
        await g.update_node(w.ids[name], CollectionNames.RECORDS.value, {"virtualRecordId": vrid})

    def edge(to_id: str, to_collection: str, **extra: object) -> dict:
        return {"from_id": w.user_key, "from_collection": CollectionNames.USERS.value,
                "to_id": to_id, "to_collection": to_collection,
                "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}

    await g.batch_create_edges(
        [edge(rid, CollectionNames.RECORDS.value, role="OWNER", type="USER") for rid in w.ids.values()]
        + [edge(w.kb_id, CollectionNames.APPS.value, role="OWNER", type="USER")],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [{"from_id": w.ids[n], "from_collection": CollectionNames.RECORDS.value,
          "to_id": w.kb_id, "to_collection": CollectionNames.APPS.value, "entityType": "KB",
          "createdAtTimestamp": now, "updatedAtTimestamp": now} for n in KB_RECORDS],
        collection=CollectionNames.BELONGS_TO.value,
    )
    await g.batch_upsert_nodes(
        [{"id": w.record_group_id, "orgId": w.org_id, "groupName": "Shared drive", "externalGroupId": f"ext-{w.record_group_id}",
          "groupType": "DRIVE", "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": w.connector_id,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.RECORD_GROUPS.value,
    )
    await g.batch_create_edges(
        [{"from_id": w.ids[n], "from_collection": CollectionNames.RECORDS.value,
          "to_id": w.record_group_id, "to_collection": CollectionNames.RECORD_GROUPS.value,
          "createdAtTimestamp": now, "updatedAtTimestamp": now} for n in ("live_shared", "trashed_shared")],
        collection=CollectionNames.BELONGS_TO.value,
    )

    def relation(parent: str, child: str, kind: str) -> dict:
        return {"from_id": w.ids[parent], "from_collection": CollectionNames.RECORDS.value,
                "to_id": w.ids[child], "to_collection": CollectionNames.RECORDS.value,
                "relationshipType": kind, "createdAtTimestamp": now, "updatedAtTimestamp": now}

    await g.batch_create_edges(
        [relation("kb_folder", "kb_child_live", "PARENT_CHILD"),
         relation("kb_folder", "kb_child_trashed", "PARENT_CHILD"),
         relation("ref_trash_q", "live", "PARENT_CHILD"),
         relation("ref_trash_q", "trashed", "PARENT_CHILD"),
         relation("live_failed", "live_shared", "LINKED_TO"),
         relation("live_failed", "trashed_shared", "LINKED_TO")],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )

    await g.batch_create_edges(
        [{"from_id": w.ids[n], "from_collection": CollectionNames.RECORDS.value,
          "to_id": w.kb_id, "to_collection": CollectionNames.APPS.value,
          "createdAtTimestamp": now, "updatedAtTimestamp": now} for n in KB_RECORDS],
        collection=CollectionNames.INHERIT_PERMISSIONS.value,
    )
    await g.batch_create_edges(
        [edge(app, CollectionNames.APPS.value, syncState="COMPLETED", lastSyncUpdate=now)
         for app in (w.connector_id, w.kb_id)],
        collection=CollectionNames.USER_APP_RELATION.value,
    )

    stored = await g.get_document(w.ids["trashed"], CollectionNames.RECORDS.value)
    assert stored is not None and stored.get("isDeleted") is True, "the trashed seed did not store"
    assert stored.get("deleteSource") == "USER" and stored.get("deletedAtTimestamp"), stored


def _ids_anywhere(payload: object) -> set[str]:
    """Every "id"/"_key"/"recordId" value in a nested response, whatever its shape."""
    found: set[str] = set()
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key in ("id", "_key", "recordId"):
                if isinstance(item.get(key), str):
                    found.add(item[key])
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)
        elif hasattr(item, "id") and isinstance(item.id, str):
            found.add(item.id)
    return found


def _ids(records: list) -> set[str]:
    out = set()
    for r in records:
        out.add(r.id if hasattr(r, "id") else (r.get("_key") or r.get("id")))
    return out


# ---------------------------------------------------------------------------
# PARAM methods: the caller picks
# ---------------------------------------------------------------------------


async def _by_external_id(w: _World, visibility: RecordVisibility) -> set[str]:
    found = set()
    for name in ("live", "trashed"):
        record = await w.graph.get_record_by_external_id(w.connector_id, w.ext(name), visibility=visibility)
        if record is not None:
            found.add(record.id)
    return found


async def _by_status(w: _World, visibility: RecordVisibility) -> set[str]:
    return _ids(await w.graph.get_records_by_status(
        w.org_id, w.connector_id, [ProgressStatus.FAILED.value], visibility=visibility,
    ))


async def _by_parent(w: _World, visibility: RecordVisibility) -> set[str]:
    return _ids(await w.graph.get_records_by_parent(w.connector_id, w.parent_ext, visibility=visibility))


async def _by_record_ids(w: _World, visibility: RecordVisibility) -> set[str]:
    return _ids(await w.graph.get_records_by_record_ids(
        [w.ids["live"], w.ids["trashed"]], w.org_id, visibility=visibility,
    ))


# method -> (probe, live names, trashed names) for the same seed
PARAM_PROBES = {
    "get_record_by_external_id": (_by_external_id, {"live"}, {"trashed"}),
    "get_records_by_status": (_by_status, {"live_failed"}, {"trashed_failed"}),
    "get_records_by_parent": (
        _by_parent,
        {"live", "live_shared", "live_failed"},
        {"trashed", "trashed_shared", "trashed_failed"},
    ),
    "get_records_by_record_ids": (_by_record_ids, {"live"}, {"trashed"}),
}


# LIVE registry methods this file calls against the real graphs, by the test that does.
# tests/unit/services/graph_db/test_record_visibility_registry.py requires every one.
EXERCISED_HERE: dict[str, str] = {
    "check_record_access_with_details": "test_access_check",
    "get_accessible_virtual_record_ids": "test_public_search_permission_map",
    "filter_accessible_virtual_record_ids": "test_search_permission_checks",
    "filter_accessible_record_ids": "test_search_permission_checks",
    "get_records_by_record_group": "test_reindex_walks",
    "get_records_by_parent_record": "test_reindex_walks",
    "get_linked_records": "test_linked_records",
    "get_records": "test_all_records_list",
    "list_all_records": "test_all_records_list",
    "list_kb_records": "test_kb_listings",
    "get_kb_children": "test_kb_listings",
    "get_folder_children": "test_kb_listings",
    "get_connector_stats": "test_connector_stats",
    "find_duplicate_records": "test_duplicates",
    "find_next_queued_duplicate": "test_next_queued_duplicate",
    "update_queued_duplicates_status": "test_queued_duplicate_status_is_not_copied_onto_the_trash",
    "get_failed_records_by_org": "test_failed_records",
    "get_failed_records_with_active_users": "test_failed_records",
    "get_record_by_weburl": "test_weburl_lookup",
    "get_entity_candidate_records": "test_entity_candidate_records",
    "get_records_by_virtual_record_id": "test_vector_delete_authority",
    "get_virtual_record_ids_shared_outside_connector": "test_content_shared_outside_a_deleted_connector",
    "get_knowledge_hub_children": "test_knowledge_hub_browse",
    "get_knowledge_hub_search": "test_knowledge_hub_search",
    "get_record_by_id": "test_point_reads_return_the_trash_with_its_state",
    "get_file_record_by_id": "test_point_reads_return_the_trash_with_its_state",
}


@pytest.mark.parametrize("method", sorted(PARAM_PROBES))
async def test_param_methods(world: _World, method: str) -> None:
    probe, live, trashed = PARAM_PROBES[method]
    live_ids = {world.ids[n] for n in live}
    trashed_ids = {world.ids[n] for n in trashed}

    assert await probe(world, RecordVisibility.LIVE) == live_ids
    assert await probe(world, RecordVisibility.DELETED) == trashed_ids
    assert await probe(world, RecordVisibility.ALL) == live_ids | trashed_ids


async def test_default_visibility_is_live(world: _World) -> None:
    g = world.graph
    assert await g.get_record_by_external_id(world.connector_id, world.ext("trashed")) is None
    assert (await g.get_record_by_external_id(world.connector_id, world.ext("live"))).id == world.ids["live"]
    assert _ids(await g.get_records_by_parent(world.connector_id, world.parent_ext)) == {
        world.ids[n] for n in ("live", "live_shared", "live_failed")
    }


# ---------------------------------------------------------------------------
# LIVE methods: the gates
# ---------------------------------------------------------------------------


async def test_search_permission_map_for_a_connector(world: _World) -> None:
    got = await world.graph._get_virtual_ids_for_connector(
        world.user_id, world.org_id, world.connector_id, raise_on_error=True
    )
    trashed_only = {world.vrids[n] for n in ("trashed", "trashed_failed", "queued_trash")}
    assert got.get(world.vrids["live"]) == world.ids["live"]
    assert not trashed_only & got.keys()
    # A VRID shared with a trashed record must resolve to the live one.
    assert got.get(world.shared_vrid) == world.ids["live_shared"]


async def test_search_permission_map_for_a_kb(world: _World) -> None:
    got = await world.graph._get_kb_virtual_ids(
        world.user_id, world.org_id, [world.kb_id], raise_on_error=True
    )
    assert got == {world.vrids[n]: world.ids[n] for n in ("kb_live", "kb_folder", "kb_child_live")}


async def test_access_check(world: _World) -> None:
    g = world.graph
    assert await g.check_record_access_with_details(world.user_id, world.org_id, world.ids["kb_live"]) is not None
    assert await g.check_record_access_with_details(world.user_id, world.org_id, world.ids["kb_trashed"]) is None


async def test_duplicates(world: _World) -> None:
    got = await world.graph.find_duplicate_records("probe-key", world.md5_dup, world.org_id)
    assert _ids(got) == {world.ids["live"]}


async def test_next_queued_duplicate(world: _World) -> None:
    g = world.graph
    assert await g.find_next_queued_duplicate(world.ids["ref_trash_q"], raise_on_error=True) is None
    live = await g.find_next_queued_duplicate(world.ids["ref_live_q"], raise_on_error=True)
    assert live is not None and (live.get("_key") or live.get("id")) == world.ids["queued_live"]


async def test_queued_duplicate_status_is_not_copied_onto_the_trash(world: _World) -> None:
    g = world.graph
    assert await g.update_queued_duplicates_status(world.ids["ref_trash_q"], "COMPLETED", "vrid-x") == 0
    stored = await g.get_document(world.ids["queued_trash"], CollectionNames.RECORDS.value)
    assert stored["indexingStatus"] == ProgressStatus.QUEUED.value


async def test_failed_records(world: _World) -> None:
    g = world.graph
    assert _ids(await g.get_failed_records_by_org(world.org_id, world.connector_id)) == {world.ids["live_failed"]}
    with_users = await g.get_failed_records_with_active_users(world.org_id, world.connector_id)
    assert _ids([row["record"] for row in with_users]) == {world.ids["live_failed"]}


async def test_weburl_lookup(world: _World) -> None:
    g = world.graph
    assert (await g.get_record_by_weburl(world.url("live"), world.org_id)).id == world.ids["live"]
    assert await g.get_record_by_weburl(world.url("trashed"), world.org_id) is None


async def test_entity_candidate_records(world: _World) -> None:
    refs = [
        {"id": world.ids[name], "type": "record", "connectorIds": [world.connector_id]}
        for name in ("live", "trashed")
    ]
    got = await world.graph.get_entity_candidate_records(refs, world.org_id)
    assert [row["_key"] for row in got[("record", world.ids["live"])]] == [world.ids["live"]]
    assert got[("record", world.ids["trashed"])] == []


async def test_vector_delete_authority(world: _World) -> None:
    got = await world.graph.get_records_by_virtual_record_id(world.shared_vrid, raise_on_error=True)
    assert set(got) == {world.ids["live_shared"]}


async def test_content_shared_outside_a_deleted_connector(world: _World) -> None:
    """Shared with a live record elsewhere: rebuilt. Shared only with the trash elsewhere: not."""
    g = world.graph
    shares = {"kb_live": "live", "kb_child_live": "trashed", "kb_trashed": "live_failed"}
    for kb_record, outside in shares.items():
        await g.update_node(
            world.ids[kb_record], CollectionNames.RECORDS.value, {"virtualRecordId": world.vrids[outside]}
        )
    got = await g.get_virtual_record_ids_shared_outside_connector(world.kb_id)
    assert set(got) == {world.vrids["live"], world.vrids["live_failed"]}, got


async def test_knowledge_hub_browse(world: _World) -> None:
    browse = world.graph.get_knowledge_hub_children(
        world.kb_id, "app", world.org_id, world.user_key, 0, 50, "name", "asc",
    )
    if isinstance(world.graph, ArangoHTTPProvider):
        with pytest.raises(Exception, match=ARANGO_APP_BROWSE_FAILS):
            await browse
        return
    got = await browse
    ids = {n.get("id") for n in got.get("nodes", [])}
    assert world.ids["kb_live"] in ids
    assert world.ids["kb_trashed"] not in ids


async def test_knowledge_hub_search(world: _World) -> None:
    """Search expands every permission arm (direct, KB app, inherited), then hydrates the page."""
    got = await world.graph.get_knowledge_hub_search(
        world.org_id, world.user_key, 0, 100, "name", "asc",
    )
    ids = {n.get("id") for n in got.get("nodes", [])}
    assert {world.ids["kb_live"], world.ids["live"]} <= ids, ids
    assert not {world.ids[n] for n in ("kb_trashed", "kb_child_trashed", "trashed", "trashed_shared", "queued_trash")} & ids


async def test_public_search_permission_map(world: _World) -> None:
    got = await world.graph.get_accessible_virtual_record_ids(world.user_id, world.org_id, raise_on_error=True)
    assert got.get(world.vrids["live"]) == world.ids["live"]
    assert got.get(world.vrids["kb_live"]) == world.ids["kb_live"]
    assert got.get(world.shared_vrid) == world.ids["live_shared"]
    trashed_only = {world.vrids[n] for n in ("trashed", "trashed_failed", "kb_trashed", "kb_child_trashed")}
    assert not trashed_only & got.keys()


async def test_search_permission_checks(world: _World) -> None:
    g = world.graph
    vrids = [world.vrids[n] for n in ("live", "trashed", "kb_live", "kb_trashed")]
    got = await g.filter_accessible_virtual_record_ids(vrids, world.user_id, world.org_id)
    assert set(got) == {world.vrids["live"], world.vrids["kb_live"]}, got
    ids = [world.ids[n] for n in ("live", "trashed", "kb_live", "kb_trashed")]
    assert await g.filter_accessible_record_ids(ids, world.user_id, world.org_id) == {
        world.ids["live"], world.ids["kb_live"],
    }


async def test_reindex_walks(world: _World) -> None:
    g = world.graph
    in_group = await g.get_records_by_record_group(world.record_group_id, world.connector_id, world.org_id, 0)
    assert _ids(in_group) == {world.ids["live_shared"]}
    under = _ids(await g.get_records_by_parent_record(world.ids["ref_trash_q"], world.connector_id, world.org_id, 1))
    assert world.ids["live"] in under and world.ids["trashed"] not in under


async def test_linked_records(world: _World) -> None:
    got = _ids_anywhere(await world.graph.get_linked_records(
        world.ids["live_failed"], world.org_id, world.user_key, ["LINKED_TO"],
    ))
    assert world.ids["live_shared"] in got and world.ids["trashed_shared"] not in got


# Pre-existing faults these listings hit, which make the answer empty. Pinned so
# the day one is fixed this file is updated and the LIVE claim is checked there too.
ARANGO_LIST_ALL_RECORDS_FAILS = "bind parameter 'limit' was not declared in the query"
ARANGO_LIST_KB_RECORDS_FAILS = "collection or view not found: user_permission"
# Main's external-collaborator branch (#3115) declares parent_rgs again inside the
# record permission probe, so Arango refuses to parse app browse. Renaming it is not
# the fix: the query then runs out of memory while the optimizer plans it.
ARANGO_APP_BROWSE_FAILS = "variable 'parent_rgs' is assigned multiple times"


async def test_all_records_list(world: _World) -> None:
    g = world.graph
    args = (world.org_id, 0, 200, None, None, None, None, None, None, None, None, "recordName", "asc", "all")
    records, total, _ = await g.list_all_records(world.user_key, *args)
    if isinstance(g, ArangoHTTPProvider):
        assert (records, total) == ([], 0), ARANGO_LIST_ALL_RECORDS_FAILS
        records, _, _ = await g.get_records(world.user_id, *args)
        assert records == [], ARANGO_LIST_ALL_RECORDS_FAILS
        return
    got = {r["id"] for r in records}
    live = {world.ids[n] for n in (*LIVE_CONNECTOR, "kb_live", "kb_child_live")}
    trashed = {world.ids[n] for n in (*TRASHED_CONNECTOR, "kb_trashed", "kb_child_trashed")}
    assert live <= got and not trashed & got and total == len(records)
    # Neo4j's get_records takes record ids, not the interface's arguments.
    with pytest.raises(TypeError):
        await g.get_records(world.user_id, *args)


async def test_kb_listings(world: _World) -> None:
    g = world.graph
    kb_records, _, _ = await g.list_kb_records(
        world.kb_id, world.user_key, world.org_id, 0, 100,
        None, None, None, None, None, None, None, "recordName", "asc",
    )
    got = _ids_anywhere(kb_records)
    if isinstance(g, ArangoHTTPProvider):
        assert kb_records == [], ARANGO_LIST_KB_RECORDS_FAILS
    else:
        assert world.ids["kb_live"] in got and world.ids["kb_child_live"] in got
        assert not {world.ids["kb_trashed"], world.ids["kb_child_trashed"]} & got

    root = _ids_anywhere(await g.get_kb_children(world.kb_id, 0, 100))
    assert world.ids["kb_live"] in root and world.ids["kb_trashed"] not in root

    folder = _ids_anywhere(await g.get_folder_children(world.kb_id, world.ids["kb_folder"], 0, 100))
    assert world.ids["kb_child_live"] in folder and world.ids["kb_child_trashed"] not in folder


async def test_connector_stats(world: _World) -> None:
    """KB stats count the collection's files; the folder is left out by design."""
    stats = await world.graph.get_connector_stats(world.org_id, world.kb_id)
    assert stats["success"] is True, stats
    assert stats["data"]["stats"]["total"] == 2, stats


# ---------------------------------------------------------------------------
# ALL methods: still find the trashed record
# ---------------------------------------------------------------------------


async def test_point_reads_return_the_trash_with_its_state(world: _World) -> None:
    g = world.graph
    record = await g.get_record_by_id(world.ids["trashed"])
    assert record is not None and record.is_deleted is True
    assert record.delete_source is DeleteSource.USER
    file_record = await g.get_file_record_by_id(world.ids["trashed"])
    assert file_record is not None and file_record.is_deleted is True


async def test_connector_delete_collects_the_trash(world: _World) -> None:
    got = await world.graph._collect_connector_entities(world.connector_id)
    keys = set(got["record_keys"])
    assert world.ids["trashed"] in keys and world.ids["live"] in keys
