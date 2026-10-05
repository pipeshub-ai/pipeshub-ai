"""Chat's foreign-key enrichment leaves tables in the trash out: real Neo4j and ArangoDB.

When a search hit is a database table, chat also pulls in the tables it is
linked to by a foreign key: their DDL, two sample rows, and their own foreign
keys. The trash keeps a dropped table's node, its foreign-key edges and its
stored content until the purge, so the enrichment has to check that each
related table is live.

The tables are written by the production sync path (``on_new_records``) and a
dropped one is trashed by the connector's own delete (``on_record_deleted``)
with ``ENABLE_SOFT_DELETE`` on:

- orders references customers and products; products references suppliers.
- While all four are live, a hit on orders pulls in customers and products,
  and products lists suppliers among its foreign keys.
- Once customers and suppliers are dropped, the same hit pulls in products
  only, and neither dropped table is named in any foreign-key list.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_soft_delete_chat_fk_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as processor_module,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import RecordType, RelatedExternalRecord, SQLTableRecord
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.chat_helpers import enrich_virtual_record_id_to_result_with_fk_children
from tests.integration.test_soft_delete_e2e import _connect_arango, _connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("soft-delete-chat-fk-it")

RECORDS = CollectionNames.RECORDS.value
REFERENCES = {"suppliers": (), "customers": (), "products": ("suppliers",), "orders": ("customers", "products")}


class _Producer:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        self.events.append(message)
        return True

    async def send_messages(self, topic: str, messages: list) -> list[bool]:
        self.events.extend(m for _key, m in messages)
        return [True] * len(messages)


class _StoredContent:
    """The blob store's processed content by virtual record id; the trash keeps it until the purge."""

    def __init__(self) -> None:
        self.content: dict[str, dict] = {}

    async def get_record_from_storage(
        self, virtual_record_id: str, org_id: str, lookup_result: dict | None = None
    ) -> dict | None:
        found = self.content.get(virtual_record_id)
        return dict(found) if found else None


@dataclass
class _World:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    org_id: str
    connector_id: str
    blobs: _StoredContent = field(default_factory=_StoredContent)
    ids: dict[str, str] = field(default_factory=dict)

    def vrid(self, name: str) -> str:
        return f"vr-{self.ids[name]}"

    def name_of(self, record_id: str) -> str:
        return next(name for name, rid in self.ids.items() if rid == record_id)

    async def chat_enrichment_of(self, hit: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        results: dict[str, Any] = {
            self.vrid(hit): {"id": self.ids[hit], "record_type": "SQL_TABLE", "record_name": hit},
        }
        flattened: list[dict[str, Any]] = [{"virtual_record_id": self.vrid(hit), "record_id": self.ids[hit]}]
        await enrich_virtual_record_id_to_result_with_fk_children(
            results, self.blobs, self.org_id, graph_provider=self.graph, flattened_results=flattened,
        )
        return results, flattened


def _table(w: _World, name: str) -> SQLTableRecord:
    table = SQLTableRecord(
        id=w.ids[name], org_id=w.org_id, record_name=name, record_type=RecordType.SQL_TABLE,
        external_record_id=f"public.{name}", external_revision_id="rev-1", version=1,
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.POSTGRESQL, connector_id=w.connector_id,
        mime_type=MimeTypes.SQL_TABLE.value, indexing_status=ProgressStatus.COMPLETED.value,
        inherit_permissions=True,
    )
    for parent in REFERENCES[name]:
        table.related_external_records.append(RelatedExternalRecord(
            external_record_id=f"public.{parent}", record_type=RecordType.SQL_TABLE, record_name=parent,
            relation_type=RecordRelations.FOREIGN_KEY, source_column=f"{parent}_id", target_column="id",
            child_table_name=f"public.{name}", parent_table_name=f"public.{parent}",
            constraint_name=f"fk_{name}_{parent}",
        ))
    return table


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    ids = [*w.ids.values(), w.connector_id]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids OR n.connectorId = $connector "
            "OPTIONAL MATCH (n)-[:IS_OF_TYPE]->(t) DETACH DELETE n, t",
            parameters={"ids": ids, "connector": w.connector_id},
        )
        return
    for collection in (RECORDS, CollectionNames.RECORD_GROUPS.value):
        ids += await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.connectorId == @c RETURN d._key", {"c": w.connector_id}
        ) or []
    for collection in (RECORDS, CollectionNames.SQL_TABLES.value, CollectionNames.RECORD_GROUPS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                  CollectionNames.IS_OF_TYPE.value, CollectionNames.RECORD_RELATIONS.value,
                  CollectionNames.INHERIT_PERMISSIONS.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
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
        monkeypatch.setattr(processor_module, "is_soft_delete_enabled", AsyncMock(return_value=True))
        suffix = uuid.uuid4().hex[:10]
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.messaging_producer = _Producer()
        w = _World(graph=graph, processor=processor, org_id=f"org-fk-{suffix}", connector_id=f"postgres-fk-{suffix}")
        processor.org_id = w.org_id
        cleanup.push_async_callback(_remove, graph, w)
        for name in REFERENCES:
            w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
        # Parents first, so each foreign key finds the table it points at.
        for name in REFERENCES:
            await processor.on_new_records([(_table(w, name), [])])
            await graph.update_node(w.ids[name], RECORDS, {"virtualRecordId": w.vrid(name)})
            w.blobs.content[w.vrid(name)] = {
                "record_name": name,
                "block_containers": {
                    "block_groups": [{"type": "table", "data": {
                        "table_summary": f"The {name} table", "ddl": f"CREATE TABLE {name} (id int)"}}],
                    "blocks": [{"type": "table_row", "data": {"row_natural_language_text": f"a row of {name}"}}],
                },
            }
        yield w


def _pulled_in(w: _World, flattened: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        w.name_of(r["record_id"]): r for r in flattened
        if (r.get("metadata") or {}).get("source") == "FK_ENRICHMENT"
    }


def _named(w: _World, relations: list[dict[str, Any]]) -> set[str]:
    return {w.name_of(r["record_id"]) for r in relations}


async def test_chat_pulls_in_live_related_tables(world: _World) -> None:
    results, flattened = await world.chat_enrichment_of("orders")

    pulled = _pulled_in(world, flattened)
    assert set(pulled) == {"customers", "products"}
    assert "CREATE TABLE customers" in pulled["customers"]["content"][0]
    assert _named(world, flattened[0]["fk_parent_relations"]) == {"customers", "products"}
    assert _named(world, pulled["products"]["fk_parent_relations"]) == {"suppliers"}
    assert results[world.vrid("customers")] is not None


async def test_chat_leaves_out_related_tables_in_the_trash(world: _World) -> None:
    for dropped in ("customers", "suppliers"):
        await world.processor.on_record_deleted(world.ids[dropped])
        stored = await world.graph.get_document(world.ids[dropped], RECORDS)
        assert stored is not None and stored.get("isDeleted") is True, f"{dropped} is in the trash"
    edges = await world.graph.get_parent_record_ids_by_relation_type(
        world.ids["orders"], RecordRelations.FOREIGN_KEY.value
    )
    assert world.ids["customers"] in {e["record_id"] for e in edges}, "the trash keeps the foreign key"

    results, flattened = await world.chat_enrichment_of("orders")

    pulled = _pulled_in(world, flattened)
    assert set(pulled) == {"products"}
    assert _named(world, flattened[0]["fk_parent_relations"]) == {"products"}
    assert _named(world, pulled["products"]["fk_parent_relations"]) == set()
    assert _named(world, pulled["products"]["fk_child_relations"]) == {"orders"}
    assert world.vrid("customers") not in results
    everything_said = repr(flattened)
    for dropped in ("customers", "suppliers"):
        assert world.ids[dropped] not in everything_said
        assert f"CREATE TABLE {dropped}" not in everything_said
