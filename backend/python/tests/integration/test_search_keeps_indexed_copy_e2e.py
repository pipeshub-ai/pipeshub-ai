"""Records waiting to be indexed again, through the search read paths, on a real Neo4j and ArangoDB.

``tests/integration/graph_db/test_search_keeps_indexed_copy_real_backends.py`` covers
the accessible-record maps. This covers the other two ways search reads access:
``filter_accessible_virtual_record_ids``, which adjudicates retrieved hits (grep,
storage search, and the container path), and ``get_accessible_containers``, which
on the container path names the gated apps and leaves every record to that check.

Seeds through the provider API, and the Arango run applies the strict records
schema first, so it also proves ``hasIndexedCopy`` is declared there.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_search_keeps_indexed_copy_e2e.py -m integration

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
ARANGO_DB = "search_keeps_indexed_copy_e2e"

logger = logging.getLogger("search-keeps-indexed-copy-it")

# name -> (indexingStatus, hasIndexedCopy or None for absent, in the trash)
STATES = {
    "indexed": (ProgressStatus.COMPLETED, True, False),
    "edited_and_queued": (ProgressStatus.QUEUED, True, False),
    "reindex_failed": (ProgressStatus.FAILED, True, False),
    "never_indexed": (ProgressStatus.NOT_STARTED, None, False),
    "now_empty": (ProgressStatus.EMPTY, False, False),
    "edited_then_trashed": (ProgressStatus.QUEUED, True, True),
}
SEARCHABLE = {"indexed", "edited_and_queued", "reindex_failed"}


@dataclass
class _World:
    graph: IGraphDBProvider
    org_id: str
    user_id: str
    user_key: str
    connector_id: str
    ids: dict[str, str] = field(default_factory=dict)
    vrids: dict[str, str] = field(default_factory=dict)


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
    await provider.ensure_schema()
    return provider


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
        cleanup.push_async_callback(graph.disconnect)
        suffix = uuid.uuid4().hex[:10]
        w = _World(
            graph=graph,
            org_id=f"org-kic-{suffix}",
            user_id=f"user-kic-{suffix}",
            user_key=f"ukey-kic-{suffix}",
            connector_id=f"drive-kic-{suffix}",
        )
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


def _file(w: _World, name: str) -> FileRecord:
    status, _, trashed = STATES[name]
    now = get_epoch_timestamp_in_ms()
    fields: dict = {
        "id": w.ids[name],
        "org_id": w.org_id,
        "record_name": f"{name}.pdf",
        "record_type": RecordType.FILE,
        "external_record_id": f"ext-{name}-{w.connector_id}",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.GOOGLE_DRIVE,
        "connector_id": w.connector_id,
        "mime_type": "application/pdf",
        "weburl": f"https://source.example/{w.connector_id}/{name}",
        "indexing_status": status.value,
        "is_file": True,
        "extension": "pdf",
        "size_in_bytes": 1024,
    }
    if trashed:
        fields.update(
            is_deleted=True, deleted_at=now - 1000, deleted_by_user_id=w.user_key,
            delete_source=DeleteSource.USER, delete_batch_id=f"batch-{w.org_id}",
        )
    return FileRecord(**fields)


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    for name in STATES:
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
        w.vrids[name] = f"vrid-{w.ids[name]}"
    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": w.user_id, "orgId": w.org_id, "email": f"{w.user_id}@example.com",
          "fullName": "Indexed Copy Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        # orgId: the connector gate counts only the apps of the caller's org.
        [{"id": w.connector_id, "orgId": w.org_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace",
          "scope": "team", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now,
          # The container path refuses an app whose vector points lack membership arrays.
          "vectorMembershipBackfilled": True}],
        collection=CollectionNames.APPS.value,
    )
    await g.batch_upsert_records([_file(w, name) for name in STATES])
    for name, (_, has_copy, _) in STATES.items():
        fields: dict = {"virtualRecordId": w.vrids[name]}
        if has_copy is not None:
            fields["hasIndexedCopy"] = has_copy
        # On ArangoDB the strict records schema refuses this unless the field is declared.
        assert await g.update_node(w.ids[name], CollectionNames.RECORDS.value, fields), name

    def edge(to_id: str, to_collection: str, **extra: object) -> dict:
        return {"from_id": w.user_key, "from_collection": CollectionNames.USERS.value,
                "to_id": to_id, "to_collection": to_collection,
                "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}

    await g.batch_create_edges(
        [edge(rid, CollectionNames.RECORDS.value, role="OWNER", type="USER") for rid in w.ids.values()],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [edge(w.connector_id, CollectionNames.APPS.value, syncState="COMPLETED", lastSyncUpdate=now)],
        collection=CollectionNames.USER_APP_RELATION.value,
    )


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    ids = [*w.ids.values(), w.user_key, w.connector_id]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value,
                       CollectionNames.USERS.value, CollectionNames.APPS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.IS_OF_TYPE.value,
                  CollectionNames.USER_APP_RELATION.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


async def test_retrieved_hits_of_a_record_waiting_to_be_indexed_are_kept(world: _World) -> None:
    got = await world.graph.filter_accessible_virtual_record_ids(
        list(world.vrids.values()), world.user_id, world.org_id
    )
    assert got == {world.vrids[n]: world.ids[n] for n in SEARCHABLE}, got


async def test_the_container_path_grants_a_record_waiting_to_be_indexed(world: _World) -> None:
    containers = await world.graph.get_accessible_containers(world.user_id, world.org_id)
    assert containers.fallback_reason is None, containers.fallback_reason
    # The container scope is the gated app; no record is granted up front.
    assert world.connector_id in containers.app_ids
    assert dict(containers.direct_records) == {}

    adjudicated = await world.graph.filter_accessible_virtual_record_ids(
        list(world.vrids.values()), world.user_id, world.org_id,
        trusted_app_ids=containers.app_ids_trusted,
        trusted_group_ids=containers.record_group_ids_trusted,
    )
    assert set(adjudicated) == {world.vrids[n] for n in SEARCHABLE}
