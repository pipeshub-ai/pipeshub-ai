"""Against real servers: taxonomy consolidation end to end on Neo4j 5.26 and
ArangoDB 3.12. Merge then unmerge, and migrate a legacy node then undo it,
asserting the graph after each step. Only a server shows that Cypher can
move a relationship (by recreate and delete), that ArangoDB accepts an
UPDATE of an edge's ``_to`` under the strict taxonomy edge schema, and
that tier-0 lookup no longer returns a merged node.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_taxonomy_consolidation_real_backends.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import Record, RecordType
from app.modules.entity_resolution.consolidation import TaxonomyConsolidator
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "entity_graph_it"
TOPICS = CollectionNames.TOPICS.value
EDGES = CollectionNames.BELONGS_TO_TOPIC.value

logger = logging.getLogger("taxonomy-consolidation-it")


class _Neo4j:
    def __init__(self, provider: Neo4jProvider, run: str) -> None:
        self.provider, self.run = provider, run

    async def q(self, query: str, **params: object) -> list[dict]:
        return await self.provider.client.execute_query(query, parameters=params)

    async def topic(self, key: str, name: str, org: str | None, created: int = 1) -> None:
        props: dict[str, Any] = {"id": key, "name": name, "createdAtTimestamp": created, "itRun": self.run}
        if org:
            props |= {"orgId": org, "normalizedName": name.casefold().replace("-", " ")}
        await self.q("CREATE (n:Topics) SET n = $props", props=props)

    async def link(self, record: str, org: str, topic: str, extracted: str | None = None) -> None:
        await self.q(
            "MERGE (r:Record {id: $rec}) SET r.orgId = $org, r.itRun = $run "
            "WITH r MATCH (t:Topics {id: $topic}) "
            "CREATE (r)-[e:BELONGS_TO_TOPIC {createdAtTimestamp: 1}]->(t) "
            "SET e.extractedName = $extracted",
            rec=record, org=org, topic=topic, run=self.run, extracted=extracted,
        )

    async def edges(self, record: str) -> list[tuple[str, str | None, str | None]]:
        rows = await self.q(
            "MATCH (:Record {id: $rec})-[e:BELONGS_TO_TOPIC]->(t:Topics) "
            "RETURN t.id AS t, e.extractedName AS x, e.mergedFrom AS m ORDER BY t",
            rec=record,
        )
        return [(r["t"], r["x"], r["m"]) for r in rows]

    async def clean(self) -> None:
        await self.q("MATCH (n) WHERE n.itRun = $run DETACH DELETE n", run=self.run)


class _Arango:
    def __init__(self, provider: ArangoHTTPProvider, run: str) -> None:
        self.provider, self.run = provider, run
        self.keys: dict[str, set[str]] = {TOPICS: set(), CollectionNames.RECORDS.value: set()}

    async def q(self, query: str, **binds: object) -> list:
        return await self.provider.http_client.execute_aql(query, binds)

    async def topic(self, key: str, name: str, org: str | None, created: int = 1) -> None:
        doc: dict[str, Any] = {"_key": key, "name": name, "createdAtTimestamp": created}
        if org:
            doc |= {"orgId": org, "normalizedName": name.casefold().replace("-", " ")}
        self.keys[TOPICS].add(key)
        await self.q(f"INSERT @d INTO {TOPICS}", d=doc)

    async def link(self, record: str, org: str, topic: str, extracted: str | None = None) -> None:
        records = CollectionNames.RECORDS.value
        if record not in self.keys[records]:
            self.keys[records].add(record)
            doc = Record(
                id=record, org_id=org, record_name="doc", record_type=RecordType.FILE,
                external_record_id=f"ext-{record}", version=0, origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.KNOWLEDGE_BASE, connector_id="c-it",
            ).to_arango_base_record()
            await self.q(f"INSERT @d INTO {records}", d=doc)
        edge = {"_from": f"{records}/{record}", "_to": f"{TOPICS}/{topic}", "createdAtTimestamp": 1,
                "extractedName": extracted}
        await self.q(f"INSERT @e INTO {EDGES}", e=edge)

    async def edges(self, record: str) -> list[tuple[str, str | None, str | None]]:
        rows = await self.q(
            f"FOR e IN {EDGES} FILTER e._from == @f SORT e._to "
            "RETURN [PARSE_IDENTIFIER(e._to).key, e.extractedName, e.mergedFrom]",
            f=f"{CollectionNames.RECORDS.value}/{record}",
        )
        return [tuple(r) for r in rows]

    async def clean(self) -> None:
        records = CollectionNames.RECORDS.value
        await self.q(
            f"FOR e IN {EDGES} FILTER PARSE_IDENTIFIER(e._from).key IN @r REMOVE e IN {EDGES}",
            r=sorted(self.keys[records]),
        )
        for collection, keys in self.keys.items():
            await self.q(f"FOR d IN {collection} FILTER d._key IN @k REMOVE d IN {collection}", k=sorted(keys))


async def _open_neo4j(monkeypatch: pytest.MonkeyPatch) -> Neo4jProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    return provider


async def _open_arango(monkeypatch: pytest.MonkeyPatch) -> ArangoHTTPProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("connect returned False")
    await provider.ensure_schema()
    return provider


@pytest.fixture(params=["neo4j", "arango"])
async def backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[Any, Any, str]]:
    open_, helper = {"neo4j": (_open_neo4j, _Neo4j), "arango": (_open_arango, _Arango)}[request.param]
    try:
        provider = await open_(monkeypatch)
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    run = uuid.uuid4().hex[:8]
    db = helper(provider, run)
    try:
        yield provider, db, run
    finally:
        await db.clean()
        if request.param == "neo4j":
            await provider.disconnect()


def _consolidator(provider: Neo4jProvider | ArangoHTTPProvider) -> TaxonomyConsolidator:
    return TaxonomyConsolidator(graph_provider=provider, entity_store=None, logger=logger)


async def test_merge_then_unmerge(backend) -> None:
    provider, db, run = backend
    org = f"org-{run}"
    win, lose = f"win-{run}", f"lose-{run}"
    await db.topic(win, "Bug bash", org, created=1)
    await db.topic(lose, "Bug-bash", org, created=2)
    await db.link(f"r1-{run}", org, lose, extracted="bug-bash")
    await db.link(f"r2-{run}", org, lose)
    await db.link(f"r2-{run}", org, win)
    await db.link(f"x1-{run}", f"other-{run}", lose)
    consolidator = _consolidator(provider)

    (group,) = await consolidator.duplicate_groups(TOPICS, org)
    assert (group.winner.key, [n.key for n in group.losers]) == (win, [lose])
    planned = await consolidator.merge(TOPICS, org, win, lose, dry_run=True)
    assert planned.edges_moved == 2
    assert await db.edges(f"r1-{run}") == [(lose, "bug-bash", None)]

    applied = await consolidator.merge(TOPICS, org, win, lose, dry_run=False)
    assert applied.edges_moved == 2
    assert await db.edges(f"r1-{run}") == [(win, "bug-bash", lose)]
    assert await db.edges(f"r2-{run}") == [(win, None, None)]  # deduped, the original kept
    assert await db.edges(f"x1-{run}") == [(lose, None, None)]  # another org's record stays
    found = await provider.find_taxonomy_nodes(TOPICS, org, ["bug bash"])
    assert [r["id"] for r in found] == [win]  # the merged node is no longer a target
    assert await consolidator.duplicate_groups(TOPICS, org) == []

    restored = await consolidator.unmerge(TOPICS, org, lose, dry_run=False)
    assert restored.edges_moved == 1
    assert await db.edges(f"r1-{run}") == [(lose, "bug-bash", None)]
    assert await db.edges(f"r2-{run}") == [(win, None, None)]


async def test_legacy_migration_then_undo(backend) -> None:
    provider, db, run = backend
    org, other = f"org-{run}", f"other-{run}"
    legacy = f"legacy-{run}"
    await db.topic(legacy, "Pricing strategy", None)
    await db.link(f"r1-{run}", org, legacy, extracted="pricing strategy")
    await db.link(f"x1-{run}", other, legacy)
    consolidator = _consolidator(provider)

    nodes = await consolidator.legacy_nodes(TOPICS, org)
    assert [(n.key, n.records) for n in nodes] == [(legacy, 1)]

    result = await consolidator.migrate_legacy(TOPICS, org, legacy, dry_run=False)
    target = taxonomy_node_key(org, TOPICS, "pricing strategy")
    if isinstance(db, _Arango):
        db.keys[TOPICS].add(target)
    else:
        await db.q("MATCH (n:Topics {id: $k}) SET n.itRun = $run", k=target, run=run)
    assert result.target_key == target and result.edges_moved == 1
    # Migration provenance is its own field; mergedFrom is for merges.
    assert await db.edges(f"r1-{run}") == [(target, "pricing strategy", None)]
    assert await db.edges(f"x1-{run}") == [(legacy, None, None)]
    assert await consolidator.legacy_nodes(TOPICS, org) == []
    found = await provider.find_taxonomy_nodes(TOPICS, org, ["pricing strategy"])
    assert [r["id"] for r in found] == [target]

    restored = await consolidator.unmigrate_legacy(TOPICS, org, legacy, target, dry_run=False)
    assert restored.edges_moved == 1
    assert await db.edges(f"r1-{run}") == [(legacy, "pricing strategy", None)]


async def test_chained_merges_keep_origins_and_undo_one_link_at_a_time(backend) -> None:
    """A into B, then B into C: each edge keeps the node it first came from,
    A redirects straight to C, and each link undoes on its own."""
    provider, db, run = backend
    org = f"org-{run}"
    a, b, c = (f"{n}-{run}" for n in "abc")
    await db.topic(a, "bug-bash", org, created=3)
    await db.topic(b, "Bug bash", org, created=2)
    await db.topic(c, "Bugbash", org, created=1)
    await db.link(f"ra-{run}", org, a, extracted="bug-bash")
    await db.link(f"rb-{run}", org, b)
    consolidator = _consolidator(provider)

    await consolidator.merge(TOPICS, org, b, a, dry_run=False)
    await consolidator.merge(TOPICS, org, c, b, dry_run=False)
    assert await db.edges(f"ra-{run}") == [(c, "bug-bash", a)]
    assert await db.edges(f"rb-{run}") == [(c, None, b)]
    (node,) = await provider.get_nodes_by_field_in(TOPICS, "id", [a], return_fields=["id", "mergedInto"])
    assert node["mergedInto"] == c

    assert (await consolidator.unmerge(TOPICS, org, a, dry_run=False)).edges_moved == 1
    assert await db.edges(f"ra-{run}") == [(a, "bug-bash", None)]
    assert await db.edges(f"rb-{run}") == [(c, None, b)]
    assert (await consolidator.unmerge(TOPICS, org, b, dry_run=False)).edges_moved == 1
    assert await db.edges(f"rb-{run}") == [(b, None, None)]


async def test_edges_move_in_batches(backend, monkeypatch: pytest.MonkeyPatch) -> None:
    """More edges than one batch all move, and the count is per edge."""
    from app.services.graph_db.arango import arango_http_provider
    from app.services.graph_db.neo4j import neo4j_provider

    monkeypatch.setattr(neo4j_provider, "_EDGE_MOVE_BATCH", 2)
    monkeypatch.setattr(arango_http_provider, "_EDGE_MOVE_BATCH", 2)
    provider, db, run = backend
    org = f"org-{run}"
    await db.topic(f"w-{run}", "Hub", org, created=1)
    await db.topic(f"l-{run}", "hub!", org, created=2)
    for i in range(5):
        await db.link(f"r{i}-{run}", org, f"l-{run}")
    await db.link(f"r0-{run}", org, f"w-{run}")  # one record already on the winner
    moved = await provider.move_taxonomy_edges(TOPICS, f"l-{run}", f"w-{run}", org, set_merged_from=f"l-{run}")
    assert moved == 5
    for i in range(5):
        assert [t for t, _, _ in await db.edges(f"r{i}-{run}")] == [f"w-{run}"]


async def test_migrate_then_merge_then_undo_both(backend) -> None:
    """Migration and merge keep separate provenance, so each undo finds its
    own edges in either order of operations."""
    provider, db, run = backend
    org = f"org-{run}"
    legacy = f"legacy-{run}"
    await db.topic(legacy, "Pricing strategy", None)
    await db.link(f"r1-{run}", org, legacy, extracted="pricing strategy")
    consolidator = _consolidator(provider)
    target = (await consolidator.migrate_legacy(TOPICS, org, legacy, dry_run=False)).target_key
    if isinstance(db, _Arango):
        db.keys[TOPICS].add(target)
    else:
        await db.q("MATCH (n:Topics {id: $k}) SET n.itRun = $run", k=target, run=run)
    winner = f"w-{run}"
    await db.topic(winner, "Pricing-strategy", org, created=0)
    await consolidator.merge(TOPICS, org, winner, target, dry_run=False)
    assert [t for t, _, _ in await db.edges(f"r1-{run}")] == [winner]

    assert (await consolidator.unmerge(TOPICS, org, target, dry_run=False)).edges_moved == 1
    assert [t for t, _, _ in await db.edges(f"r1-{run}")] == [target]
    assert (await consolidator.unmigrate_legacy(TOPICS, org, legacy, target, dry_run=False)).edges_moved == 1
    assert await db.edges(f"r1-{run}") == [(legacy, "pricing strategy", None)]


async def test_moving_to_a_missing_node_is_refused(backend) -> None:
    provider, db, run = backend
    org = f"org-{run}"
    await db.topic(f"a-{run}", "Alpha", org)
    await db.link(f"r1-{run}", org, f"a-{run}")
    with pytest.raises(ValueError, match="not found"):
        await provider.move_taxonomy_edges(TOPICS, f"a-{run}", f"missing-{run}", org, set_merged_from=f"a-{run}")
    assert [t for t, _, _ in await db.edges(f"r1-{run}")] == [f"a-{run}"]


async def test_a_shared_legacy_hub_moves_only_this_orgs_edges_in_batches(
    backend, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edges are found once and moved by id, so batching across a hub that
    other orgs share still moves every edge of this org and none of theirs."""
    from app.services.graph_db.arango import arango_http_provider
    from app.services.graph_db.neo4j import neo4j_provider

    monkeypatch.setattr(neo4j_provider, "_EDGE_MOVE_BATCH", 2)
    monkeypatch.setattr(arango_http_provider, "_EDGE_MOVE_BATCH", 2)
    provider, db, run = backend
    org, other = f"org-{run}", f"other-{run}"
    legacy = f"legacy-{run}"
    await db.topic(legacy, "Pricing strategy", None)
    for i in range(5):
        await db.link(f"x{i}-{run}", other, legacy)
        await db.link(f"r{i}-{run}", org, legacy)
    consolidator = _consolidator(provider)

    result = await consolidator.migrate_legacy(TOPICS, org, legacy, dry_run=False)
    target = result.target_key
    if isinstance(db, _Arango):
        db.keys[TOPICS].add(target)
    else:
        await db.q("MATCH (n:Topics {id: $k}) SET n.itRun = $run", k=target, run=run)
    assert result.edges_moved == 5
    for i in range(5):
        assert [t for t, _, _ in await db.edges(f"r{i}-{run}")] == [target]
        assert [t for t, _, _ in await db.edges(f"x{i}-{run}")] == [legacy]

    restored = await consolidator.unmigrate_legacy(TOPICS, org, legacy, target, dry_run=False)
    assert restored.edges_moved == 5
    for i in range(5):
        assert [t for t, _, _ in await db.edges(f"r{i}-{run}")] == [legacy]


async def test_edges_never_move_onto_another_orgs_node_or_a_legacy_one(backend) -> None:
    provider, db, run = backend
    org = f"org-{run}"
    await db.topic(f"a-{run}", "Alpha", org)
    await db.topic(f"theirs-{run}", "Alpha", f"other-{run}")
    await db.topic(f"legacy-{run}", "Alpha", None)
    await db.link(f"r1-{run}", org, f"a-{run}")
    for to in (f"theirs-{run}", f"legacy-{run}"):
        with pytest.raises(ValueError, match="not a node of org"):
            await provider.move_taxonomy_edges(TOPICS, f"a-{run}", to, org, set_merged_from=f"a-{run}")
    assert [t for t, _, _ in await db.edges(f"r1-{run}")] == [f"a-{run}"]


async def test_legacy_nodes_are_paged_in_key_order_with_this_orgs_counts(backend) -> None:
    """Pages walk the legacy nodes by key and count only this org's
    records; a legacy node only another org uses is not listed."""
    provider, db, run = backend
    org, other = f"org-{run}", f"other-{run}"
    keys = [f"legacy{i}-{run}" for i in range(5)]
    for i, key in enumerate(keys):
        await db.topic(key, f"Legacy {i}", None)
        if i != 2:
            await db.link(f"r{i}a-{run}", org, key)
            await db.link(f"r{i}b-{run}", org, key)
        await db.link(f"x{i}-{run}", other, key)
    seen, after = [], None
    while True:
        page = await provider.find_legacy_taxonomy_nodes(TOPICS, org, 2, after_key=after)
        seen += [(r["_key"], r["records"]) for r in page if r["_key"].endswith(run)]
        if len(page) < 2:
            break
        after = page[-1]["_key"]
    assert seen == [(k, 2) for i, k in enumerate(keys) if i != 2]
