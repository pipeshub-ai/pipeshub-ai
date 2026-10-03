"""A record group whose records are in the trash, against a real Neo4j and a real ArangoDB.

When a source removes a whole group, the connector deletes the group's records
and then the group, which takes the group's edges with it, the records'
BELONGS_TO links included. With ``ENABLE_SOFT_DELETE`` on, those records are
only in the trash, so ``on_record_group_deleted`` keeps the group and its
edges while any of them still belongs to it; a restore then puts them back in
it, and the purge removes the group later. With the flag off, it is deleted
as on main.

- PostgreSQL: a schema dropped at the source, on full and incremental sync.
- Dropbox: a team folder permanently deleted.
- With the trash on, a group holding only live records is still deleted.

Writes records through ``DataSourceEntitiesProcessor.on_new_records`` over a
real ``GraphDataStore``, so the group and BELONGS_TO edge are the ones a sync
creates, and calls the connectors' own removal methods.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_record_group_trash_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
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
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as processor_module,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.registry.filters import MultiselectOperator
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.postgres.connector import PostgreSQLConnector
from app.models.entities import FileRecord, RecordGroupType, RecordType, SQLTableRecord
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.models.entities import Record
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "record_group_trash_it"

logger = logging.getLogger("record-group-trash-it")

RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value


@dataclass
class _World:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    org_id: str
    connector_id: str

    async def group(self, external_id: str) -> dict | None:
        group = await self.processor.get_record_group_by_external_id(self.connector_id, external_id)
        return None if group is None else await self.graph.get_document(group.id, GROUPS)

    async def belongs_to(self, record_id: str, group_id: str) -> dict | None:
        return await self.graph.get_edge(record_id, RECORDS, group_id, GROUPS, CollectionNames.BELONGS_TO.value)


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


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $cid OPTIONAL MATCH (n)-[:IS_OF_TYPE]->(t) DETACH DELETE n, t",
            parameters={"cid": w.connector_id},
        )
        return
    keys = await graph.http_client.execute_aql(
        f"FOR d IN {RECORDS} FILTER d.connectorId == @cid RETURN d._key", {"cid": w.connector_id}
    )
    groups = await graph.http_client.execute_aql(
        f"FOR d IN {GROUPS} FILTER d.connectorId == @cid RETURN d._key", {"cid": w.connector_id}
    )
    ids = [*keys, *groups]
    for collection in (RECORDS, GROUPS, CollectionNames.FILES.value, CollectionNames.SQL_TABLES.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                  CollectionNames.IS_OF_TYPE.value, CollectionNames.INHERIT_PERMISSIONS.value,
                  CollectionNames.RECORD_RELATIONS.value):
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
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.messaging_producer = AsyncMock()
        w = _World(graph=graph, processor=processor, org_id=f"org-rgt-{suffix}", connector_id=f"conn-rgt-{suffix}")
        processor.org_id = w.org_id
        cleanup.push_async_callback(_remove, graph, w)
        yield w


def _trash(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(processor_module, "is_soft_delete_enabled", AsyncMock(return_value=on))


async def _synced(w: _World, record: Record) -> dict:
    """Write ``record`` as a sync does; returns its group, which that write creates."""
    await w.processor.on_new_records([(record, [])])
    group = await w.group(record.external_record_group_id)
    assert group is not None
    assert await w.belongs_to(record.id, group.get("_key") or group["id"]) is not None
    return group


async def _assert_kept_or_gone(w: _World, record_id: str, group: dict, external_group_id: str, trash_on: bool) -> None:
    group_id = group.get("_key") or group["id"]
    doc = await w.graph.get_document(record_id, RECORDS)
    if trash_on:
        assert doc is not None and doc["isDeleted"] is True and doc["deleteSource"] == "CONNECTOR", doc
        assert await w.group(external_group_id) is not None, "the trash keeps the group"
        assert await w.belongs_to(record_id, group_id) is not None, "the trash keeps the BELONGS_TO edge"
    else:
        assert doc is None
        assert await w.group(external_group_id) is None
        assert await w.belongs_to(record_id, group_id) is None


def _table(w: _World, schema: str, name: str) -> SQLTableRecord:
    return SQLTableRecord(
        id=str(uuid.uuid4()), org_id=w.org_id, record_name=name, record_type=RecordType.SQL_TABLE,
        record_group_type=RecordGroupType.SQL_NAMESPACE.value, external_record_group_id=schema,
        external_record_id=f"{schema}.{name}", external_revision_id="rev-1", version=1,
        origin=OriginTypes.CONNECTOR.value, connector_name=Connectors.POSTGRESQL, connector_id=w.connector_id,
        mime_type=MimeTypes.SQL_TABLE.value, indexing_status=ProgressStatus.COMPLETED.value,
        inherit_permissions=True,
    )


def _postgres(w: _World) -> SimpleNamespace:
    connector = SimpleNamespace(
        data_entities_processor=w.processor, data_store_provider=w.processor.data_store_provider,
        connector_id=w.connector_id, logger=logger, sync_stats=SimpleNamespace(errors=0), database_name="shop",
        _fetch_schemas=AsyncMock(return_value=[]), _passes_filter=PostgreSQLConnector._passes_filter,
    )
    connector._schema_group_id = lambda name: PostgreSQLConnector._schema_group_id(connector, name)
    return connector


@pytest.mark.parametrize("sync", ["full", "incremental"])
@pytest.mark.parametrize("trash_on", [True, False])
async def test_a_dropped_postgres_schema_keeps_its_group_while_its_table_is_in_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch, sync: str, trash_on: bool,
) -> None:
    _trash(monkeypatch, trash_on)
    table = _table(world, "sales", "orders")
    group = await _synced(world, table)
    connector = _postgres(world)

    if sync == "full":
        assert await PostgreSQLConnector._remove_stale_tables(connector, set()) == {}
        await PostgreSQLConnector._remove_stale_schema_groups(connector, synced_schemas=[])
    else:
        assert await PostgreSQLConnector._handle_deleted_tables(connector, [table.external_record_id]) == {
            table.external_record_id
        }
        op = MultiselectOperator.IN.value
        await PostgreSQLConnector._remove_stale_schema_groups(connector, filters=(None, op, None, op))

    assert connector.sync_stats.errors == 0
    await _assert_kept_or_gone(world, table.id, group, "sales", trash_on)


def _dropbox_file(w: _World, team_folder_id: str) -> FileRecord:
    return FileRecord(
        id=str(uuid.uuid4()), org_id=w.org_id, record_name="budget.xlsx", record_type=RecordType.FILE,
        record_group_type=RecordGroupType.DRIVE.value, external_record_group_id=team_folder_id,
        external_record_id=f"id:{uuid.uuid4().hex[:12]}", external_revision_id="rev-1", version=1,
        origin=OriginTypes.CONNECTOR.value, connector_name=Connectors.DROPBOX, connector_id=w.connector_id,
        mime_type="application/vnd.ms-excel", indexing_status=ProgressStatus.COMPLETED.value,
        is_file=True, extension="xlsx", inherit_permissions=True,
    )


def _team_folder_deleted(team_folder_id: str) -> SimpleNamespace:
    folder = SimpleNamespace(
        display_name="Finance", path=SimpleNamespace(namespace_relative=SimpleNamespace(ns_id=team_folder_id))
    )
    return SimpleNamespace(assets=[SimpleNamespace(is_folder=lambda: True, get_folder=lambda: folder)])


@pytest.mark.parametrize("trash_on", [True, False])
async def test_a_deleted_dropbox_team_folder_keeps_its_group_while_its_file_is_in_the_trash(
    world: _World, monkeypatch: pytest.MonkeyPatch, trash_on: bool,
) -> None:
    _trash(monkeypatch, trash_on)
    team_folder_id = f"ns-{uuid.uuid4().hex[:8]}"
    file = _dropbox_file(world, team_folder_id)
    group = await _synced(world, file)
    connector = SimpleNamespace(data_entities_processor=world.processor, connector_id=world.connector_id, logger=logger)
    connector._extract_folder_info_from_event = lambda event: DropboxConnector._extract_folder_info_from_event(
        connector, event
    )

    await DropboxConnector._handle_record_updates(
        connector, SimpleNamespace(is_deleted=True, external_record_id=file.external_record_id)
    )
    await DropboxConnector._handle_record_group_deleted_event(connector, _team_folder_deleted(team_folder_id))

    await _assert_kept_or_gone(world, file.id, group, team_folder_id, trash_on)


async def test_with_the_trash_on_a_group_holding_only_live_records_is_still_deleted(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _trash(monkeypatch, True)
    table = _table(world, "staging", "events")
    await _synced(world, table)

    assert await world.processor.on_record_group_deleted("staging", world.connector_id) is True

    assert await world.group("staging") is None
    assert (await world.graph.get_document(table.id, RECORDS))["isDeleted"] is not True
