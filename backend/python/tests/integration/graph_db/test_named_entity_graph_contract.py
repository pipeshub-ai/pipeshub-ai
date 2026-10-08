"""Named-entity graph methods against a real Neo4j and a real ArangoDB.

One parameterized contract: every case must return the same answer on both
backends. Writes go through the production NamedEntityGraphWriter, so the
strict Arango schemas and the Neo4j MERGE paths are exercised as shipped.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_named_entity_graph_contract.py -m integration
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import (
    Mention,
    NamedEntity,
)
from app.modules.named_entities.domain.values import DateRangeValue, MoneyValue
from app.modules.named_entities.graph_ops import NamedEntityQuery
from app.modules.named_entities.graph_writer import (
    NamedEntityGraphWriter,
    node_document,
)
from app.modules.named_entities.keys import named_entity_key
from app.modules.named_entities.resolution import ResolvedEntity
from app.modules.named_entities.sweep import NamedEntitySweeper
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

logger = logging.getLogger("named-entity-graph-it")
ARANGO_DB = "named_entity_graph_it"
NODES = CollectionNames.NAMED_ENTITIES.value
EDGES = CollectionNames.MENTIONS_ENTITY.value
VALUES = CollectionNames.VALUE_MENTIONS.value
RETRIES = CollectionNames.NAMED_ENTITY_PERSIST_RETRIES.value
RECORDS = CollectionNames.RECORDS.value
RECORD_ENTITY_FIELDS = (
    "entityExtractionStatus", "lastEntityExtractionTimestamp", "entityExtractionStrategy", "entityExtractorVersion",
)


async def _record_untouched(provider: IGraphDBProvider, record: str) -> bool:
    doc = await provider.get_document(record, RECORDS)
    return not any(doc.get(name) is not None for name in RECORD_ENTITY_FIELDS)


@contextlib.asynccontextmanager
async def _backend(name: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[IGraphDBProvider]:
    try:
        provider = await (connect_neo4j(logger, monkeypatch) if name == "neo4j" else connect_arango(logger, ARANGO_DB))
    except Exception as exc:
        backend_unavailable(name, exc)
    try:
        yield provider
    finally:
        await provider.disconnect()


@pytest.fixture(params=["neo4j", "arango"])
async def graph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, str, str]]:
    org_id = f"org-ne-{uuid.uuid4().hex[:10]}"
    async with _backend(request.param, monkeypatch) as provider:
        try:
            yield provider, org_id, request.param
        finally:
            await _cleanup(provider, org_id, request.param)


async def _cleanup(provider: IGraphDBProvider, org_id: str, backend: str) -> None:
    if backend == "neo4j":
        await provider.execute_query(
            "MATCH (n) WHERE n.orgId = $org DETACH DELETE n", {"org": org_id},
        )
        return
    await provider.execute_query(
        f"FOR e IN {EDGES} FILTER e.orgId == @org REMOVE e IN {EDGES}", {"org": org_id},
    )
    for collection in (NODES, VALUES, RETRIES, RECORDS):
        await provider.execute_query(
            f"FOR d IN {collection} FILTER d.orgId == @org REMOVE d IN {collection}", {"org": org_id},
        )


def _ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _mention(surface: str) -> Mention:
    return Mention(block_index=0, char_start=0, char_end=len(surface), surface=surface, extractor="value")


def _quarter(org_id: str) -> ResolvedEntity:
    entity = NamedEntity(
        kind=EntityKind.DATE_RANGE,
        display_name="Q3 FY25",
        norm_key="2025-07-01/2025-10-01",
        value=DateRangeValue(start_ms=_ms(2025, 7, 1), end_ms=_ms(2025, 10, 1), granularity="quarter", timex="2025-Q3"),
        mentions=[_mention("Q3 FY25")],
    )
    return ResolvedEntity(entity=entity, graph_key=named_entity_key(org_id, entity.kind.value, entity.norm_key))


def _amount(org_id: str, amount: str, currency: str = "USD") -> ResolvedEntity:
    value = Decimal(amount)
    entity = NamedEntity(
        kind=EntityKind.CURRENCY,
        display_name=f"{currency} {amount}",
        norm_key=f"{currency}:{amount}",
        value=MoneyValue(amount=value, amount_float=float(value), currency=currency),
        mentions=[_mention(f"{currency} {amount}")],
    )
    return ResolvedEntity(entity=entity, graph_key=named_entity_key(org_id, entity.kind.value, entity.norm_key))


def _org(org_id: str, name: str) -> ResolvedEntity:
    entity = NamedEntity(kind=EntityKind.ORGANIZATION, display_name=name, norm_key=name.casefold(), mentions=[_mention(name)])
    return ResolvedEntity(entity=entity, graph_key=named_entity_key(org_id, entity.kind.value, entity.norm_key))


async def _records(provider: IGraphDBProvider, org_id: str, count: int, connector_id: str = "") -> list[str]:
    ids = [f"{org_id}-rec-{connector_id}{i}" for i in range(count)]
    await provider.batch_upsert_nodes([
        {
            "id": record_id, "orgId": org_id, "recordName": f"Doc {i}",
            "externalRecordId": record_id, "recordType": "FILE", "origin": "CONNECTOR",
            "connectorId": connector_id or f"{org_id}-conn", "virtualRecordId": f"v-{record_id}",
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
        }
        for i, record_id in enumerate(ids)
    ], RECORDS)
    return ids


def _writer(provider: IGraphDBProvider) -> NamedEntityGraphWriter:
    return NamedEntityGraphWriter(provider, GraphDataStore(logger, provider), logger)


async def test_schema_creates_named_entity_indexes(graph) -> None:
    provider, _, backend = graph
    if backend == "neo4j":
        rows = await provider.execute_query("SHOW INDEXES YIELD name RETURN name", {})
        names = {row["name"] for row in rows or []}
        assert {
            "named_entity_org_kind_norm", "mentions_entity_org", "value_mention_record", "value_mention_org_kind_start",
        } <= names
        return
    fields = [tuple(index.get("fields", [])) for index in await provider.http_client.get_indexes(NODES)]
    assert ("orgId", "kind", "normKey") in fields
    values = await provider.http_client.get_indexes(VALUES)
    assert ("recordId",) in {tuple(index.get("fields", [])) for index in values}
    sparse = {tuple(index["fields"]) for index in values if index.get("sparse")}
    assert {("orgId", "kind", "startMs"), ("orgId", "currency", "amountFloat")} <= sparse


async def test_concurrent_create_if_absent_converges_on_one_node(graph) -> None:
    provider, org_id, _ = graph
    resolved = _org(org_id, "Acme")
    now = get_epoch_timestamp_in_ms()
    await asyncio.gather(*(
        provider.create_named_entities_if_absent([node_document(org_id, resolved, now + i)]) for i in range(6)
    ))
    found = await provider.find_named_entities(org_id, "organization", ["acme"])
    assert len(found) == 1


async def test_aliases_are_partitioned_per_kind(graph) -> None:
    provider, org_id, _ = graph
    org = _org(org_id, "Acme")
    await provider.create_named_entities_if_absent([node_document(org_id, org, get_epoch_timestamp_in_ms())])
    await provider.add_named_entity_aliases(org_id, "organization", org.graph_key, ["ACME Corp"], ["acme corp"])
    assert len(await provider.find_named_entities(org_id, "organization", ["acme corp"])) == 1
    assert await provider.find_named_entities(org_id, "product", ["acme corp"]) == []
    assert await provider.find_named_entities(f"{org_id}-other", "organization", ["acme corp"]) == []


async def test_range_and_overlap_queries_return_the_same_records(graph) -> None:
    provider, org_id, _ = graph
    first, second = await _records(provider, org_id, 2)
    writer = _writer(provider)
    await writer.write(org_id, first, [_quarter(org_id), _amount(org_id, "1250")])
    await writer.write(org_id, second, [_amount(org_id, "90000"), _amount(org_id, "1250", "EUR")])

    august = NamedEntityQuery(kinds=["date_range"], date_start_ms=_ms(2025, 8, 1), date_end_ms=_ms(2025, 8, 2))
    hits = await provider.get_records_for_named_entities(org_id, None, august)
    assert {hit["recordId"] for hit in hits["hits"]} == {first}
    assert hits["truncated"] is False

    after = NamedEntityQuery(kinds=["date_range"], date_start_ms=_ms(2025, 10, 1), date_end_ms=_ms(2025, 11, 1))
    assert (await provider.get_records_for_named_entities(org_id, None, after))["hits"] == []

    usd = NamedEntityQuery(kinds=["currency"], amount_min=1000, amount_max=100_000, currency="USD")
    hits = await provider.get_records_for_named_entities(org_id, None, usd)
    assert {hit["recordId"] for hit in hits["hits"]} == {first, second}

    eur = NamedEntityQuery(currency="EUR", amount_min=0, amount_max=10_000)
    assert {hit["recordId"] for hit in (await provider.get_records_for_named_entities(org_id, None, eur))["hits"]} == {second}

    any_currency = NamedEntityQuery(kinds=["currency"], amount_min=1000, amount_max=2000)
    hits = await provider.get_records_for_named_entities(org_id, None, any_currency)
    assert {hit["recordId"] for hit in hits["hits"]} == {first, second}

    # Constraints hold per record: a date and an amount are two entities of one record.
    amount_and_date = NamedEntityQuery(
        amount_min=1000, amount_max=2000, date_start_ms=_ms(2025, 8, 1), date_end_ms=_ms(2025, 8, 2),
    )
    hits = await provider.get_records_for_named_entities(org_id, None, amount_and_date)
    assert [hit["recordId"] for hit in hits["hits"]] == [first]
    no_record_has_both = NamedEntityQuery(
        amount_min=90_000, amount_max=90_000, date_start_ms=_ms(2025, 8, 1), date_end_ms=_ms(2025, 8, 2),
    )
    assert (await provider.get_records_for_named_entities(org_id, None, no_record_has_both))["hits"] == []


async def test_name_filter_narrows_and_an_oversized_match_is_truncated(graph) -> None:
    provider, org_id, _ = graph
    first, second = await _records(provider, org_id, 2)
    writer = _writer(provider)
    await writer.write(org_id, first, [_org(org_id, "Acme Robotics")])
    await writer.write(org_id, second, [_org(org_id, "Globex")])

    by_name = NamedEntityQuery(kinds=["organization"], name_prefix="acme")
    hits = await provider.get_records_for_named_entities(org_id, None, by_name)
    assert [
        (hit["recordId"], hit["virtualRecordId"], hit["connectorId"]) for hit in hits["hits"]
    ] == [(first, f"v-{first}", f"{org_id}-conn")]
    page = await provider.query_named_entities(org_id, NamedEntityQuery(name_prefix="ACME"))
    assert [entity["name"] for entity in page["entities"]] == ["Acme Robotics"]

    everything = NamedEntityQuery(kinds=["organization"])
    capped = await provider.get_records_for_named_entities(org_id, None, everything, limit=1)
    assert capped == {"hits": [], "truncated": True}


async def test_entity_filter_lookups_read_through_indexes(graph) -> None:
    """These run on every entity-filtered search: a collection scan here reads
    every org's mention edges or value rows."""
    provider, org_id, backend = graph
    if backend != "arango":
        pytest.skip("ArangoDB query plans only")
    from app.modules.named_entities.graph_ops import (
        _AQL_BIND,
        NODE_RECORDS_AQL,
        VALUE_RECORDS_AQL,
    )

    (first,) = await _records(provider, org_id, 1)
    acme = _org(org_id, "Acme")
    await _writer(provider).write(org_id, first, [acme, _amount(org_id, "1250")])

    client = provider.http_client
    session = await client._get_session()

    async def plan(statement: str, sent: dict) -> list[dict]:
        binds = {name: value for name, value in sent.items() if name in set(_AQL_BIND.findall(statement))}
        async with session.post(
            f"{client.base_url}/_db/{client.database}/_api/explain", json={"query": statement, "bindVars": binds}
        ) as resp:
            return (await resp.json())["plan"]["nodes"]

    nodes = await plan(NODE_RECORDS_AQL, {"orgId": org_id, "targets": [f"{NODES}/{acme.graph_key}"], "limit": 10})
    assert [node["type"] for node in nodes if node.get("collection") == EDGES] == ["IndexNode"]
    values = await plan(VALUE_RECORDS_AQL, {
        "orgId": org_id, "within": False, "candidates": [], "limit": 10, "kinds": [],
        "dateStart": None, "dateEnd": None, "amountMin": 1000, "amountMax": 2000, "currency": "USD",
        "quantityMin": None, "quantityMax": None, "dimension": "", "percentMin": None, "percentMax": None,
    })
    assert [node["type"] for node in values if node.get("collection") == VALUES] == ["IndexNode"]

    hits = await provider.get_records_for_named_entities(org_id, [acme.graph_key], None)
    assert [hit["recordId"] for hit in hits["hits"]] == [first]
    other_org = await provider.get_records_for_named_entities(f"{org_id}-other", [acme.graph_key], None)
    assert other_org == {"hits": [], "truncated": False}
    usd = NamedEntityQuery(amount_min=1000, amount_max=2000, currency="USD")
    assert (await provider.get_records_for_named_entities(f"{org_id}-other", None, usd))["hits"] == []


async def test_writer_reconciles_and_leaves_the_record_fields_alone(graph) -> None:
    provider, org_id, _ = graph
    (record,) = await _records(provider, org_id, 1)
    writer = _writer(provider)
    await writer.write(org_id, record, [_org(org_id, "Acme"), _org(org_id, "Globex")])
    await writer.write(org_id, record, [_org(org_id, "Acme")])
    names = sorted(row["name"] for row in await provider.get_named_entities_for_record(record))
    assert names == ["Acme"]
    assert await _record_untouched(provider, record)


async def test_copy_then_clear_then_orphan_cleanup(graph) -> None:
    provider, org_id, _ = graph
    source, target = await _records(provider, org_id, 2)
    writer = _writer(provider)
    await writer.write(org_id, source, [_org(org_id, "Acme"), _quarter(org_id)])

    copied = await provider.copy_named_entity_mentions(source, target)
    assert copied == 2
    assert await provider.copy_named_entity_mentions(source, target) == 2
    rows = await provider.get_named_entities_for_record(target)
    assert sorted(row["kind"] for row in rows) == ["date_range", "organization"]
    assert all(row.get("mentionCount") == 1 for row in rows)
    quarter = NamedEntityQuery(date_start_ms=_ms(2025, 8, 1), date_end_ms=_ms(2025, 8, 2))
    hits = await provider.get_records_for_named_entities(org_id, None, quarter)
    assert {hit["recordId"] for hit in hits["hits"]} == {source, target}

    await writer.clear_for_record(source)
    assert await provider.get_named_entities_for_record(source) == []
    assert await provider.delete_orphan_named_entities(org_id) == []

    await writer.clear_for_record(target)
    assert await provider.get_named_entities_for_record(target) == []
    removed = await provider.delete_orphan_named_entities(org_id)
    assert len(removed) == 1
    assert await provider.find_named_entities(org_id, "organization", ["acme"]) == []


# ---------------------------------------------------------------- one-way-door fixes

HOUR_MS = 3_600_000
GRACE_MS = 24 * HOUR_MS


def _email(org_id: str, address: str) -> ResolvedEntity:
    entity = NamedEntity(
        kind=EntityKind.EMAIL, display_name=address, norm_key=f"email:{address}",
        mentions=[Mention(block_index=0, char_start=0, char_end=len(address), surface=address, extractor="pattern")],
    )
    return ResolvedEntity(entity=entity, graph_key=named_entity_key(org_id, entity.kind.value, entity.norm_key))


async def _value_count(provider: IGraphDBProvider, backend: str, org_id: str) -> int:
    if backend == "neo4j":
        rows = await provider.execute_query(
            "MATCH (v:ValueMention {orgId: $org}) RETURN count(v) AS n", {"org": org_id},
        )
        return int(rows[0]["n"])
    rows = await provider.execute_query(
        f"FOR v IN {VALUES} FILTER v.orgId == @org COLLECT WITH COUNT INTO n RETURN n", {"org": org_id},
    )
    return int(rows[0])


async def _edge_count(provider: IGraphDBProvider, backend: str, org_id: str) -> int:
    if backend == "neo4j":
        rows = await provider.execute_query(
            "MATCH ()-[e:MENTIONS_ENTITY {orgId: $org}]->() RETURN count(e) AS n", {"org": org_id},
        )
        return int(rows[0]["n"])
    rows = await provider.execute_query(
        f"FOR e IN {EDGES} FILTER e.orgId == @org COLLECT WITH COUNT INTO n RETURN n", {"org": org_id},
    )
    return int(rows[0])


async def _node_count(provider: IGraphDBProvider, backend: str, org_id: str) -> int:
    if backend == "neo4j":
        rows = await provider.execute_query(
            "MATCH (n:NamedEntity {orgId: $org}) RETURN count(n) AS n", {"org": org_id},
        )
        return int(rows[0]["n"])
    rows = await provider.execute_query(
        f"FOR n IN {NODES} FILTER n.orgId == @org COLLECT WITH COUNT INTO c RETURN c", {"org": org_id},
    )
    return int(rows[0])


async def test_records_a_caller_cannot_read_change_neither_hits_nor_too_broad(graph) -> None:
    provider, org_id, _ = graph
    readable, *unreadable = await _records(provider, org_id, 4)
    writer = _writer(provider)
    for record in (readable, *unreadable):
        await writer.write(org_id, record, [_org(org_id, "Acme"), _amount(org_id, "1250")])

    acme_and_amount = NamedEntityQuery(name_prefix="acme", amount_min=1000, amount_max=2000)
    assert (await provider.get_records_for_named_entities(org_id, None, acme_and_amount, limit=2))["truncated"] is True
    within = await provider.get_records_for_named_entities(org_id, None, acme_and_amount, limit=2, within=[readable])
    assert within == {"hits": [{"recordId": readable, "virtualRecordId": f"v-{readable}", "connectorId": f"{org_id}-conn"}], "truncated": False}
    nothing_readable = await provider.get_records_for_named_entities(org_id, None, acme_and_amount, within=[f"{org_id}-none"])
    assert nothing_readable == {"hits": [], "truncated": False}


async def test_value_rows_of_a_deleted_record_are_invisible_then_swept(graph) -> None:
    provider, org_id, backend = graph
    kept, deleted = await _records(provider, org_id, 2)
    writer = _writer(provider)
    for record in (kept, deleted):
        await writer.write(org_id, record, [_amount(org_id, "1250")])
    if backend == "neo4j":
        await provider.execute_query("MATCH (r:Record {id: $id}) DETACH DELETE r", {"id": deleted})
    else:
        await provider.execute_query(f"REMOVE @key IN {RECORDS}", {"key": deleted})

    usd = NamedEntityQuery(amount_min=1000, amount_max=2000, currency="USD")
    assert [hit["recordId"] for hit in (await provider.get_records_for_named_entities(org_id, None, usd))["hits"]] == [kept]
    result = await NamedEntitySweeper(provider, AsyncMock(), logger_=logger).sweep_org(org_id)
    assert result.values_removed == 1
    assert await _value_count(provider, backend, org_id) == 1


async def test_identity_and_edge_uniqueness_are_enforced_by_the_database(graph) -> None:
    provider, _, backend = graph
    if backend == "neo4j":
        rows = await provider.execute_query("SHOW CONSTRAINTS YIELD name RETURN name", {})
        assert "namedentity_id_unique" in {row["name"] for row in rows or []}
        rows = await provider.execute_query("SHOW INDEXES YIELD name RETURN name", {})
        assert "named_entity_org_orphaned" in {row["name"] for row in rows or []}
        return
    unique = [index for index in await provider.http_client.get_indexes(EDGES) if index.get("unique")]
    assert ["_from", "_to"] in [index["fields"] for index in unique]
    fields = [index["fields"] for index in await provider.http_client.get_indexes(NODES)]
    assert ["orgId", "orphanedAt"] in fields


async def _drop_unique_mention_index(provider: IGraphDBProvider) -> None:
    client = provider.http_client
    for index in await client.get_indexes(EDGES):
        if index.get("unique") and index["fields"] == ["_from", "_to"]:
            session = await client._get_session()
            async with session.delete(f"{client.base_url}/_db/{client.database}/_api/index/{index['id']}") as resp:
                assert resp.status == 200, await resp.text()


async def test_existing_duplicate_edges_are_collapsed_so_the_unique_index_can_be_built(graph) -> None:
    provider, org_id, backend = graph
    if backend != "arango":
        pytest.skip("ArangoDB only: Neo4j locks both ends on MERGE")
    from app.modules.named_entities.graph_ops import ensure_unique_mentions

    (record,) = await _records(provider, org_id, 1)
    entity = _org(org_id, "Acme")
    await provider.create_named_entities_if_absent([node_document(org_id, entity, get_epoch_timestamp_in_ms())])
    await _drop_unique_mention_index(provider)
    try:
        for stamp in (1, 3, 2):
            await provider.execute_query(
                f"INSERT {{ _from: @f, _to: @t, orgId: @org, createdAtTimestamp: 1, updatedAtTimestamp: @s, mentionCount: @s }} INTO {EDGES}",
                {"f": f"{RECORDS}/{record}", "t": f"{NODES}/{entity.graph_key}", "org": org_id, "s": stamp},
            )
        assert await _edge_count(provider, backend, org_id) == 3
        assert await ensure_unique_mentions(provider.http_client) is True
    finally:
        await ensure_unique_mentions(provider.http_client)
    assert await _edge_count(provider, backend, org_id) == 1
    rows = await provider.get_named_entities_for_record(record)
    assert [row["mentionCount"] for row in rows] == [3]


async def test_writing_one_record_concurrently_leaves_one_edge_per_entity(graph) -> None:
    provider, org_id, backend = graph
    (record,) = await _records(provider, org_id, 1)
    writer = _writer(provider)
    entities = [_org(org_id, "Acme"), _org(org_id, "Globex"), _quarter(org_id)]
    await asyncio.gather(*(
        writer.write(org_id, record, entities) for _ in range(4)
    ))
    assert await _edge_count(provider, backend, org_id) == 2
    assert await _node_count(provider, backend, org_id) == 2
    assert await _value_count(provider, backend, org_id) == 1
    assert await _record_untouched(provider, record)


async def test_a_popular_entity_written_by_many_records_at_once_stays_one_node(graph) -> None:
    provider, org_id, backend = graph
    records = await _records(provider, org_id, 50)
    writer = _writer(provider)
    await asyncio.gather(*(
        writer.write(org_id, record, [_org(org_id, "Acme"), _quarter(org_id)])
        for record in records
    ))
    # Acme is one shared node; the quarter is a row per record, so no record waits on another's.
    assert await _node_count(provider, backend, org_id) == 1
    assert await _edge_count(provider, backend, org_id) == 50
    assert await _value_count(provider, backend, org_id) == 50
    assert all([await _record_untouched(provider, record) for record in records])


async def test_copying_never_crosses_an_org_boundary(graph) -> None:
    provider, org_id, backend = graph
    other = f"{org_id}-other"
    (source,) = await _records(provider, org_id, 1)
    (foreign,) = await _records(provider, other, 1)
    try:
        await _writer(provider).write(org_id, source, [_org(org_id, "Acme")])
        await provider.copy_named_entity_mentions(source, foreign)
        assert await provider.get_named_entities_for_record(foreign) == []
    finally:
        await _cleanup(provider, other, backend)


async def test_reserved_schema_fields_are_accepted(graph) -> None:
    provider, org_id, backend = graph
    entity = _org(org_id, "Acme")
    await provider.create_named_entities_if_absent([node_document(org_id, entity, get_epoch_timestamp_in_ms())])
    assert await provider.mark_orphan_named_entities(org_id, 123) == 1
    if backend == "neo4j":
        await provider.execute_query(
            "MATCH (n:NamedEntity {id: $k}) SET n.mergedInto = 'survivor', n.keyScheme = 2", {"k": entity.graph_key},
        )
    else:
        await provider.execute_query(
            f"UPDATE @k WITH {{ mergedInto: 'survivor', keyScheme: 2 }} IN {NODES}", {"k": entity.graph_key},
        )
    (found,) = await provider.find_named_entities(org_id, "organization", ["acme"])
    assert (found["mergedInto"], found["keyScheme"], found["orphanedAt"]) == ("survivor", 2, 123)


async def test_fields_a_later_release_adds_do_not_block_updates(graph) -> None:
    """After a rollback this build's schemas are reapplied; documents a newer build
    wrote with fields this build does not know must still take its updates."""
    provider, org_id, backend = graph
    if backend != "arango":
        pytest.skip("ArangoDB schema validation only")
    (record,) = await _records(provider, org_id, 1)
    acme = _org(org_id, "Acme")
    await _writer(provider).write(org_id, record, [acme, _amount(org_id, "1250")])
    future = {"assertionId": "a-1", "confidence": 0.9, "validFrom": 1}
    edge_key = (await provider.execute_query(
        f"FOR e IN {EDGES} FILTER e._from == @from LIMIT 1 RETURN e._key", {"from": f"{RECORDS}/{record}"},
    ))[0]
    value_key = (await provider.execute_query(
        f"FOR v IN {VALUES} FILTER v.recordId == @record LIMIT 1 RETURN v._key", {"record": record},
    ))[0]
    for collection, key in ((NODES, acme.graph_key), (EDGES, edge_key), (VALUES, value_key)):
        await provider.execute_query(f"UPDATE @key WITH @future IN {collection}", {"key": key, "future": future})
        await provider.execute_query(
            f"UPDATE @key WITH {{ updatedAtTimestamp: 2 }} IN {collection}", {"key": key},
        )
    await _writer(provider).write(org_id, record, [acme, _amount(org_id, "1250")])


async def _merge(provider: IGraphDBProvider, backend: str, loser: str, winner: str) -> None:
    if backend == "neo4j":
        await provider.execute_query("MATCH (n:NamedEntity {id: $k}) SET n.mergedInto = $w", {"k": loser, "w": winner})
    else:
        await provider.execute_query(f"UPDATE @k WITH {{ mergedInto: @w }} IN {NODES}", {"k": loser, "w": winner})


async def _merged_chain(provider: IGraphDBProvider, backend: str, org_id: str) -> tuple[str, list[str], str]:
    """Acme Inc → Acme Corp → Acme, with only the last one mentioned, as after two merges."""
    (record,) = await _records(provider, org_id, 1)
    old, middle, survivor = (_org(org_id, name) for name in ("Acme Inc", "Acme Corp", "Acme"))
    now = get_epoch_timestamp_in_ms()
    await provider.create_named_entities_if_absent([node_document(org_id, item, now) for item in (old, middle)])
    await _writer(provider).write(org_id, record, [survivor])
    await _merge(provider, backend, old.graph_key, middle.graph_key)
    await _merge(provider, backend, middle.graph_key, survivor.graph_key)
    return record, [old.graph_key, middle.graph_key], survivor.graph_key


async def test_a_merged_id_still_finds_the_records_of_the_node_it_became(graph) -> None:
    provider, org_id, backend = graph
    record, (old, middle), survivor = await _merged_chain(provider, backend, org_id)
    redirects = await provider.resolve_named_entity_redirects(org_id, [old, middle, survivor])
    assert redirects == {old: survivor, middle: survivor, survivor: survivor}
    assert await provider.resolve_named_entity_redirects(f"{org_id}-other", [old]) == {old: old}
    result = await provider.get_records_for_named_entities(org_id, [old], NamedEntityQuery(), limit=10)
    assert [hit["recordId"] for hit in result["hits"]] == [record]


async def test_the_sweep_keeps_a_merged_node_so_its_redirect_survives(graph) -> None:
    provider, org_id, backend = graph
    _record, merged, survivor = await _merged_chain(provider, backend, org_id)
    clock = [1_000 * HOUR_MS]
    sweeper = _sweeper(provider, clock, AsyncMock())
    await sweeper.sweep_org(org_id)
    clock[0] += 2 * GRACE_MS
    assert (await sweeper.sweep_org(org_id)).deleted == 0
    assert await provider.resolve_named_entity_redirects(org_id, merged) == dict.fromkeys(merged, survivor)


async def test_a_sweep_delete_waiting_on_a_writer_keeps_the_node_the_writer_linked(graph) -> None:
    """Neo4j: the delete matches the node as an orphan, then waits for the lock the
    writer's open transaction holds. Once the writer commits the link, the node has
    a mention and must stay, with its edge."""
    provider, org_id, backend = graph
    if backend != "neo4j":
        pytest.skip("Arango raises a write conflict on the stale snapshot instead")
    (record,) = await _records(provider, org_id, 1)
    acme = _org(org_id, "Acme")
    await provider.create_named_entities_if_absent([node_document(org_id, acme, 1)])
    assert await provider.mark_orphan_named_entities(org_id, 100) == 1

    async with provider.client.driver.session(database=provider.client.database) as session:
        tx = await session.begin_transaction()
        await tx.run(
            "MATCH (n:NamedEntity {id: $k}) SET n.orphanedAt = null "
            "WITH n MATCH (r:Record {id: $r}) MERGE (r)-[:MENTIONS_ENTITY {orgId: $org}]->(n)",
            {"k": acme.graph_key, "r": record, "org": org_id},
        )
        sweep = asyncio.create_task(
            provider.delete_orphan_named_entities(org_id, 10, marked_before_ms=200, entity_ids=[acme.graph_key]),
        )
        await asyncio.sleep(1.0)
        assert not sweep.done(), "the delete should be waiting on the writer's lock"
        await tx.commit()
        deleted = await asyncio.wait_for(sweep, timeout=30)

    assert deleted == []
    assert [row["name"] for row in await provider.get_named_entities_for_record(record)] == ["Acme"]
    (node,) = await provider.find_named_entities(org_id, "organization", ["acme"])
    assert "_sweepLock" not in node


async def test_an_orphan_delete_keeps_an_alias_another_entity_still_uses(graph) -> None:
    provider, org_id, backend = graph
    if backend != "neo4j":
        pytest.skip("ArangoDB keeps aliases on the node itself")
    (record,) = await _records(provider, org_id, 1)
    gone, kept = _org(org_id, "Acme Robotics"), _org(org_id, "Acme Foods")
    await provider.create_named_entities_if_absent([node_document(org_id, item, 1) for item in (gone, kept)])
    for item in (gone, kept):
        await provider.add_named_entity_aliases(org_id, "organization", item.graph_key, ["Acme"], ["acme"])
    await _writer(provider).write(org_id, record, [kept])

    assert await provider.delete_orphan_named_entities(org_id, 10) == [gone.graph_key]

    (found,) = await provider.find_named_entities(org_id, "organization", ["acme"])
    assert found["id"] == kept.graph_key


async def test_a_failed_persist_is_retried_with_a_growing_backoff(graph) -> None:
    provider, org_id, _backend = graph
    record = f"{org_id}-rec"
    assert await provider.schedule_named_entity_persist_retry(org_id, record, "DeadlockDetected") == 1
    assert await provider.get_due_named_entity_persist_retries(10) == []  # due a minute from now

    assert await provider.schedule_named_entity_persist_retry(org_id, record, "DeadlockDetected") == 2
    graph_ops = provider._named_entity_graph()
    later = get_epoch_timestamp_in_ms() + 10 * HOUR_MS
    (row,) = [r for r in await graph_ops.due_persist_retries(later, 50) if r["recordId"] == record]
    assert (row["orgId"], row["attempts"]) == (org_id, 2)
    assert row["dueAt"] - get_epoch_timestamp_in_ms() > 90_000  # doubled from the one-minute base

    assert await provider.clear_named_entity_persist_retry(record, row["dueAt"] - 1) is False
    assert await provider.clear_named_entity_persist_retry(record, row["dueAt"]) is True
    assert [r for r in await graph_ops.due_persist_retries(later, 50) if r["recordId"] == record] == []


async def _pending_retry(provider: IGraphDBProvider, org_id: str, record: str) -> int:
    await provider.schedule_named_entity_persist_retry(org_id, record, "DeadlockDetected")
    later = get_epoch_timestamp_in_ms() + 10 * HOUR_MS
    (row,) = [r for r in await provider._named_entity_graph().due_persist_retries(later, 50) if r["recordId"] == record]
    return row["dueAt"]


async def _names(provider: IGraphDBProvider, record: str) -> list[str]:
    return sorted(str(row.get("name")) for row in await provider.get_named_entities_for_record(record))


async def test_a_retry_rescheduled_after_its_marker_went_keeps_counting(graph) -> None:
    provider, org_id, _backend = graph
    record = f"{org_id}-rec"
    assert await provider.schedule_named_entity_persist_retry(org_id, record, "Boom", 5) == 6


async def test_a_retry_that_read_an_older_extraction_never_lands_over_a_newer_write(graph) -> None:
    """The retry read extraction A; a reindex then wrote B, which drops the retry in
    its own transaction. The retry's write of A must not commit."""
    from app.modules.named_entities.graph_writer import PersistRetrySupersededError

    provider, org_id, _backend = graph
    (record,) = await _records(provider, org_id, 1)
    due = await _pending_retry(provider, org_id, record)
    writer = _writer(provider)
    await writer.write(org_id, record, [_org(org_id, "Globex"), _amount(org_id, "2000")])

    with pytest.raises(PersistRetrySupersededError):
        await writer.write(org_id, record, [_org(org_id, "Acme"), _amount(org_id, "1250")], retry_due=due)

    assert await _names(provider, record) == ["Globex", "USD 2000"]


async def test_a_retry_that_is_still_pending_lands_and_ends(graph) -> None:
    provider, org_id, _backend = graph
    (record,) = await _records(provider, org_id, 1)
    due = await _pending_retry(provider, org_id, record)

    await _writer(provider).write(org_id, record, [_org(org_id, "Acme"), _amount(org_id, "1250")], retry_due=due)

    assert await _names(provider, record) == ["Acme", "USD 1250"]
    later = get_epoch_timestamp_in_ms() + 10 * HOUR_MS
    assert [r for r in await provider._named_entity_graph().due_persist_retries(later, 50) if r["recordId"] == record] == []


async def test_a_retry_rescheduled_after_it_was_read_does_not_land(graph) -> None:
    from app.modules.named_entities.graph_writer import PersistRetrySupersededError

    provider, org_id, _backend = graph
    (record,) = await _records(provider, org_id, 1)
    first = await _pending_retry(provider, org_id, record)
    await provider.schedule_named_entity_persist_retry(org_id, record, "DeadlockDetected")

    with pytest.raises(PersistRetrySupersededError):
        await _writer(provider).write(org_id, record, [_org(org_id, "Acme")], retry_due=first)
    assert await _names(provider, record) == []


async def test_a_retry_that_deadlocks_after_its_claim_lands_on_the_next_attempt(graph, monkeypatch) -> None:
    """On Neo4j without explicit transactions the claim commits at once; on ArangoDB it
    rolls back with the failed attempt. Either way the next attempt must land."""
    from app.modules.named_entities import write_retry
    from app.modules.transformers.edge_reconciler import EdgeReconciler

    class _Deadlock(Exception):
        pass

    provider, org_id, _backend = graph
    (record,) = await _records(provider, org_id, 1)
    due = await _pending_retry(provider, org_id, record)
    real = EdgeReconciler.reconcile
    failures = [_Deadlock("DeadlockDetected")]

    async def reconcile_once_failing(self, *args, **kwargs) -> None:
        if failures:
            raise failures.pop()
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(EdgeReconciler, "reconcile", reconcile_once_failing)
    monkeypatch.setattr(write_retry, "_backoff", AsyncMock())
    monkeypatch.setattr(
        type(provider), "is_named_entity_write_retryable", lambda self, error: isinstance(error, _Deadlock),
    )

    await _writer(provider).write(org_id, record, [_org(org_id, "Acme"), _amount(org_id, "1250")], retry_due=due)

    assert await _names(provider, record) == ["Acme", "USD 1250"]
    later = get_epoch_timestamp_in_ms() + 10 * HOUR_MS
    assert [r for r in await provider._named_entity_graph().due_persist_retries(later, 50) if r["recordId"] == record] == []


def _sweeper(provider: IGraphDBProvider, clock: list[int], vectors) -> NamedEntitySweeper:
    return NamedEntitySweeper(
        provider, vectors, grace_ms=GRACE_MS, batch_size=50, now_ms=lambda: clock[0], logger_=logger,
    )


async def test_orphan_sweep_waits_out_the_grace_period_then_removes_node_and_vector(graph) -> None:
    provider, org_id, backend = graph
    gone, kept = await _records(provider, org_id, 2)
    writer = _writer(provider)
    await writer.write(org_id, gone, [_org(org_id, "Acme"), _email(org_id, "a@example.com")])
    await writer.write(org_id, kept, [_org(org_id, "Acme"), _org(org_id, "Globex")])
    await writer.clear_for_record(gone)
    vectors = AsyncMock()
    clock = [1_000 * HOUR_MS]
    sweeper = _sweeper(provider, clock, vectors)

    first = await sweeper.sweep_org(org_id)
    assert (first.marked, first.deleted) == (1, 0)
    assert await _node_count(provider, backend, org_id) == 3

    clock[0] += GRACE_MS - 1
    assert (await sweeper.sweep_org(org_id)).deleted == 0

    clock[0] += 2
    third = await sweeper.sweep_org(org_id)
    assert third.deleted == 1
    assert await provider.find_named_entities(org_id, "email", ["email:a@example.com"]) == []  # noqa: E501
    assert len(await provider.find_named_entities(org_id, "organization", ["acme"])) == 1
    assert sorted(row["name"] for row in await provider.get_named_entities_for_record(kept)) == ["Acme", "Globex"]
    (call,) = vectors.delete_entities.await_args_list
    assert call.args[:2] == (org_id, "named_entity")
    assert len(call.args[2]) == 1


async def test_a_node_relinked_during_the_grace_period_survives_the_sweep(graph) -> None:
    provider, org_id, backend = graph
    first, second = await _records(provider, org_id, 2)
    writer = _writer(provider)
    await writer.write(org_id, first, [_org(org_id, "Acme")])
    await writer.clear_for_record(first)
    clock = [1_000 * HOUR_MS]
    sweeper = _sweeper(provider, clock, AsyncMock())
    assert (await sweeper.sweep_org(org_id)).marked == 1

    await writer.write(org_id, second, [_org(org_id, "Acme")])
    clock[0] += 2 * GRACE_MS
    result = await sweeper.sweep_org(org_id)
    # The writer took the mark back itself when it claimed the node.
    assert (result.cleared, result.deleted) == (0, 0)
    assert len(await provider.find_named_entities(org_id, "organization", ["acme"])) == 1
    (found,) = await provider.find_named_entities(org_id, "organization", ["acme"])
    assert found.get("orphanedAt") is None


async def test_the_sweep_leaves_other_orgs_alone(graph) -> None:
    provider, org_id, backend = graph
    other = f"{org_id}-other"
    (mine,) = await _records(provider, org_id, 1)
    (theirs,) = await _records(provider, other, 1)
    try:
        for org, record in ((org_id, mine), (other, theirs)):
            writer = _writer(provider)
            await writer.write(org, record, [_org(org, "Acme")])
            await writer.clear_for_record(record)
        clock = [1_000 * HOUR_MS]
        sweeper = _sweeper(provider, clock, AsyncMock())
        await sweeper.sweep_org(org_id)
        clock[0] += 2 * GRACE_MS
        assert (await sweeper.sweep_org(org_id)).deleted == 1
        assert await _node_count(provider, backend, other) == 1
    finally:
        await _cleanup(provider, other, backend)


async def test_an_edge_left_pointing_at_a_deleted_node_is_removed(graph) -> None:
    provider, org_id, backend = graph
    (record,) = await _records(provider, org_id, 1)
    if backend == "neo4j":
        assert await provider.delete_dangling_named_entity_mentions(["missing"]) == 0
        return
    await provider.execute_query(
        f"INSERT {{ _from: @f, _to: @t, orgId: @org, createdAtTimestamp: 1, mentionCount: 1 }} INTO {EDGES}",
        {"f": f"{RECORDS}/{record}", "t": f"{NODES}/ghost", "org": org_id},
    )
    assert await provider.delete_dangling_named_entity_mentions(["ghost"]) == 1
    assert await _edge_count(provider, backend, org_id) == 0


async def test_an_edge_to_a_node_created_again_is_a_live_mention_and_stays(graph) -> None:
    provider, org_id, backend = graph
    (record,) = await _records(provider, org_id, 1)
    entity = _org(org_id, "Acme")
    await provider.create_named_entities_if_absent([node_document(org_id, entity, get_epoch_timestamp_in_ms())])
    assert await provider.delete_orphan_named_entities(org_id) == [entity.graph_key]
    await _writer(provider).write(org_id, record, [entity])

    assert await provider.delete_dangling_named_entity_mentions([entity.graph_key]) == 0
    assert [row["name"] for row in await provider.get_named_entities_for_record(record)] == ["Acme"]


async def test_claiming_a_marked_node_takes_it_out_of_the_sweep_before_it_is_linked(graph) -> None:
    provider, org_id, _ = graph
    entity = _org(org_id, "Acme")
    doc = node_document(org_id, entity, get_epoch_timestamp_in_ms())
    await provider.create_named_entities_if_absent([doc])
    assert await provider.mark_orphan_named_entities(org_id, 1) == 1

    await provider.create_named_entities_if_absent([doc])
    (found,) = await provider.find_named_entities(org_id, "organization", ["acme"])
    assert found.get("orphanedAt") is None
    assert await provider.delete_orphan_named_entities(org_id, marked_before_ms=10**15) == []


async def test_finding_orphans_deletes_nothing_and_the_delete_keeps_to_the_ids_found(graph) -> None:
    provider, org_id, backend = graph
    acme, globex = _org(org_id, "Acme"), _org(org_id, "Globex")
    now = get_epoch_timestamp_in_ms()
    await provider.create_named_entities_if_absent([node_document(org_id, e, now) for e in (acme, globex)])
    assert await provider.mark_orphan_named_entities(org_id, 1) == 2

    found = await provider.find_orphan_named_entities(org_id, marked_before_ms=1)
    assert sorted(found) == sorted([acme.graph_key, globex.graph_key])
    assert await _node_count(provider, backend, org_id) == 2
    assert await provider.delete_orphan_named_entities(
        org_id, marked_before_ms=1, entity_ids=[globex.graph_key]
    ) == [globex.graph_key]
    assert await provider.delete_orphan_named_entities(org_id, marked_before_ms=1, entity_ids=[]) == []
    assert await provider.find_orphan_named_entities(org_id, marked_before_ms=1) == [acme.graph_key]


async def test_without_a_vector_store_kinds_with_vector_points_are_kept(graph) -> None:
    provider, org_id, _ = graph
    acme, quarter = _org(org_id, "Acme"), _quarter(org_id)
    now = get_epoch_timestamp_in_ms()
    await provider.create_named_entities_if_absent([node_document(org_id, e, now) for e in (acme, quarter)])
    clock = [1_000 * HOUR_MS]
    sweeper = _sweeper(provider, clock, None)
    await sweeper.sweep_org(org_id)
    clock[0] += 2 * GRACE_MS

    assert (await sweeper.sweep_org(org_id)).deleted == 1
    assert await provider.find_named_entities(org_id, "date_range", [quarter.entity.norm_key]) == []
    assert await provider.find_orphan_named_entities(org_id, marked_before_ms=clock[0]) == [acme.graph_key]


async def test_a_node_linked_again_while_it_is_swept_keeps_its_edge(graph) -> None:
    provider, org_id, _ = graph
    gone, linking = await _records(provider, org_id, 2)
    writer = _writer(provider)
    acme, globex = _org(org_id, "Acme"), _org(org_id, "Globex")
    await writer.write(org_id, gone, [acme, globex])
    await writer.clear_for_record(gone)
    clock = [1_000 * HOUR_MS]
    vectors = AsyncMock()
    sweeper = _sweeper(provider, clock, vectors)
    await sweeper.sweep_org(org_id)
    clock[0] += 2 * GRACE_MS

    async def a_record_links_acme(*_args) -> None:
        await writer.write(org_id, linking, [acme])

    vectors.delete_entities.side_effect = a_record_links_acme
    result = await sweeper.sweep_org(org_id)

    assert (result.deleted, result.relinked) == (1, 1)
    assert [row["name"] for row in await provider.get_named_entities_for_record(linking)] == ["Acme"]
    assert await provider.find_named_entities(org_id, "organization", ["globex"]) == []


async def test_a_failed_vector_delete_leaves_the_node_for_the_next_sweep(graph) -> None:
    provider, org_id, backend = graph
    entity = _org(org_id, "Acme")
    await provider.create_named_entities_if_absent([node_document(org_id, entity, get_epoch_timestamp_in_ms())])
    clock = [1_000 * HOUR_MS]
    vectors = AsyncMock()
    sweeper = _sweeper(provider, clock, vectors)
    await sweeper.sweep_org(org_id)
    clock[0] += 2 * GRACE_MS

    vectors.delete_entities.side_effect = RuntimeError("vector store down")
    result = await sweeper.sweep_org(org_id)
    assert (result.deleted, result.vector_failed) == (0, True)
    assert await _node_count(provider, backend, org_id) == 1

    vectors.delete_entities.side_effect = None
    assert (await sweeper.sweep_org(org_id)).deleted == 1
    assert await _node_count(provider, backend, org_id) == 0


async def _dangling_count(provider: IGraphDBProvider, backend: str, org_id: str) -> int:
    if backend == "neo4j":
        return 0
    rows = await provider.execute_query(
        f"FOR e IN {EDGES} FILTER e.orgId == @org AND DOCUMENT(e._to) == null COLLECT WITH COUNT INTO n RETURN n",
        {"org": org_id},
    )
    return int(rows[0])


async def test_sweeps_racing_writers_never_lose_a_mention(graph) -> None:
    """Writers keep linking and unlinking two entities while sweeps with a short
    grace period delete them whenever they fall idle."""
    provider, org_id, backend = graph
    records = await _records(provider, org_id, 8)
    writer = _writer(provider)
    entities = [_org(org_id, "Acme"), _org(org_id, "Globex")]
    sweeper = NamedEntitySweeper(provider, AsyncMock(), grace_ms=300, batch_size=50, logger_=logger)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 4
    deleted = 0

    async def sweep_until_done() -> None:
        nonlocal deleted
        while loop.time() < deadline:
            deleted += (await sweeper.sweep_org(org_id)).deleted
            await asyncio.sleep(0.05)

    async def churn(index: int, record: str) -> None:
        turn = index
        while loop.time() < deadline:
            if turn % 2:
                await writer.clear_for_record(record)
                await asyncio.sleep(0.4)
            else:
                await writer.write(org_id, record, entities)
            turn += 1

    await asyncio.gather(sweep_until_done(), sweep_until_done(), *(churn(i, r) for i, r in enumerate(records)))
    await asyncio.gather(*(
        writer.write(org_id, record, entities) for record in records
    ))

    # How many deletes land depends on timing; the interleaving that matters is
    # pinned by test_a_sweep_delete_waiting_on_a_writer_keeps_the_node_the_writer_linked.
    logger.info("racing sweeps deleted %d nodes", deleted)
    for record in records:
        rows = await provider.get_named_entities_for_record(record)
        assert sorted(row["name"] for row in rows) == ["Acme", "Globex"], record
    assert await _edge_count(provider, backend, org_id) == 2 * len(records)
    assert await _dangling_count(provider, backend, org_id) == 0
    assert await _node_count(provider, backend, org_id) == 2


async def test_orphan_delete_without_a_cutoff_still_removes_unmarked_orphans(graph) -> None:
    provider, org_id, backend = graph
    entity = _org(org_id, "Acme")
    await provider.create_named_entities_if_absent([node_document(org_id, entity, get_epoch_timestamp_in_ms())])
    assert await provider.delete_orphan_named_entities(org_id, marked_before_ms=1) == []
    assert await provider.delete_orphan_named_entities(org_id) == [entity.graph_key]


async def test_records_this_release_writes_stay_updatable_under_the_previous_schema(graph) -> None:
    """The rollback contract. An older build reapplies its strict records schema on
    start; every record this release linked, cleared or copied must still take that
    build's updates. A record carrying the entity-extraction fields would not, which is
    why their writer ships a release after the schema."""
    provider, org_id, backend = graph
    if backend != "arango":
        pytest.skip("ArangoDB schema validation only")
    import copy

    from app.schema.arango.documents import record_schema

    previous = copy.deepcopy(record_schema)
    for name in RECORD_ENTITY_FIELDS:
        previous["rule"]["properties"].pop(name)
    written, cleared, copied, stamped = await _records(provider, org_id, 4)
    writer = _writer(provider)
    await writer.write(org_id, written, [_org(org_id, "Acme")])
    await writer.write(org_id, cleared, [_org(org_id, "Acme")])
    await writer.clear_for_record(cleared)
    await provider.copy_named_entity_mentions(written, copied)
    assert await provider.update_node(stamped, RECORDS, {"entityExtractionStatus": "COMPLETED"})
    client = provider.http_client
    assert await client.update_collection_schema(RECORDS, previous)
    try:
        for record in (written, cleared, copied):
            assert await provider.update_node(record, RECORDS, {"recordName": "renamed"}), record
        with pytest.raises(Exception, match="schema|validation|1620"):
            await provider.update_node(stamped, RECORDS, {"recordName": "renamed"})
    finally:
        assert await client.update_collection_schema(RECORDS, record_schema)
