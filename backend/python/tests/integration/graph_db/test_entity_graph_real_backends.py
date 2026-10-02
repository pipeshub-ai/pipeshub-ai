"""Entity-graph queries against a real Neo4j 5.26 and a real ArangoDB 3.12.

The unit tests mock the database, so they pin query text, not behaviour.
These check what only a server can show:

- Neo4j's schema setup (``ensure_schema``, run at connector-service start)
  creates the TaxonomyAlias uniqueness constraint, the server enforces it, and
  alias lookups are served from it instead of scanning a label.
- Neo4j concurrent alias writes do not overwrite each other.
- The Neo4j record-group inheritance walk stays on record groups: it does not
  continue through the records under a group (and their attachments).
- On ArangoDB, records that create the same canonical node, and add aliases
  to it, enrich concurrently without errorNum 1200, and the belongsTo* edges
  they write (with extractedName) pass the strict edge schema.
- On both, a record whose writes to a new topic arrive while another record's
  create or alias update of it is still uncommitted completes its enrichment.

Needs the graph services, and skips cleanly when they are not reachable:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_entity_graph_real_backends.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
from app.models.blocks import SemanticMetadata
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.models import (
    TOPIC,
    EntityResolution,
    ResolutionMode,
    ResolvedEntity,
)
from app.modules.transformers.graphdb import GraphDBTransformer
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "entity_graph_it"
TOPICS = CollectionNames.TOPICS.value

logger = logging.getLogger("entity-graph-it")


# ---------------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------------


@pytest.fixture
async def neo4j(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Neo4jProvider, str]]:
    async with _neo4j_backend(monkeypatch) as backend:
        yield backend


@contextlib.asynccontextmanager
async def _neo4j_backend(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Neo4jProvider, str]]:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
    except Exception as exc:
        pytest.skip(f"Neo4j not available at {NEO4J_URI}: {exc}")
    # connect() does not create schema; the connector service runs this at start.
    await provider.ensure_schema()
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org_id
    finally:
        await provider.client.execute_query(
            "MATCH (n) WHERE n.orgId = $org OR n.itOrg = $org DETACH DELETE n",
            parameters={"org": org_id},
        )
        await provider.disconnect()


async def _profile(provider: Neo4jProvider, query: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Total db hits and the operators a query planned, from PROFILE."""
    async with provider.client.driver.session(database="neo4j") as session:
        result = await session.run(f"PROFILE {query}", parameters)
        await result.data()
        summary = await result.consume()
    operators: list[str] = []

    def walk(plan: dict[str, Any]) -> int:
        operators.append(plan.get("operatorType", ""))
        return int(plan.get("dbHits", 0)) + sum(walk(child) for child in plan.get("children", []))

    return {"db_hits": walk(summary.profile), "operators": operators}


def _capture_queries(provider: Neo4jProvider) -> list[tuple[str, dict[str, Any]]]:
    captured: list[tuple[str, dict[str, Any]]] = []
    real = provider.client.execute_query

    async def recording(
        query: str, parameters: dict[str, Any] | None = None, txn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        captured.append((query, dict(parameters or {})))
        return await real(query, parameters=parameters, txn_id=txn_id)

    provider.client.execute_query = recording  # type: ignore[method-assign]
    return captured


class TestNeo4jTaxonomyAliases:
    async def test_schema_setup_creates_the_alias_uniqueness_constraint(self, neo4j) -> None:
        provider, _ = neo4j
        await provider.client.execute_query("DROP CONSTRAINT taxonomyalias_key_unique IF EXISTS")

        await provider.ensure_schema()

        rows = await provider.client.execute_query(
            "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
            "WHERE name = 'taxonomyalias_key_unique' RETURN type, labelsOrTypes, properties"
        )
        assert rows == [{
            "type": "UNIQUENESS",
            "labelsOrTypes": ["TaxonomyAlias"],
            "properties": ["orgId", "collection", "normalized"],
        }]

    async def test_the_constraint_is_enforced(self, neo4j) -> None:
        provider, org_id = neo4j
        create = "CREATE (:TaxonomyAlias {orgId: $org, collection: 'topics', normalized: 'dup'})"
        await provider.client.execute_query(create, parameters={"org": org_id})
        with pytest.raises(Exception, match="(?i)already exists|constraint"):
            await provider.client.execute_query(create, parameters={"org": org_id})

    async def test_alias_lookup_seeks_alias_nodes(self, neo4j) -> None:
        provider, org_id = neo4j
        key = taxonomy_node_key(org_id, TOPICS, "onboarding")
        await provider.create_taxonomy_node_if_absent(TOPICS, {
            "id": key, "name": "Onboarding", "normalizedName": "onboarding", "orgId": org_id,
        })
        await provider.add_taxonomy_aliases(
            TOPICS, key, ["Onboarding checklist", "New hire onboarding"],
            ["onboarding checklist", "new hire onboarding"], org_id=org_id,
        )
        captured = _capture_queries(provider)

        rows = await provider.find_taxonomy_nodes(TOPICS, org_id, ["new hire onboarding"])

        assert [r["id"] for r in rows] == [key]
        query, parameters = captured[-1]
        profile = await _profile(provider, query, parameters)
        # The alias branch seeks the constraint's index instead of scanning a label.
        assert not any(op.startswith("NodeByLabelScan") for op in profile["operators"]), profile
        assert any("Seek" in op for op in profile["operators"]), profile

    async def test_concurrent_alias_writes_keep_every_alias(self, neo4j) -> None:
        """Reading the lists without the node lock let the later SET drop the
        other writer's alias."""
        provider, org_id = neo4j
        key = taxonomy_node_key(org_id, TOPICS, "release")
        await provider.create_taxonomy_node_if_absent(TOPICS, {
            "id": key, "name": "Release", "normalizedName": "release", "orgId": org_id,
        })
        writers = 12

        await asyncio.gather(*(
            provider.add_taxonomy_aliases(TOPICS, key, [f"Release {i}"], [f"release {i}"], org_id=org_id)
            for i in range(writers)
        ))

        (node,) = await provider.client.execute_query(
            "MATCH (n:Topics {id: $key}) "
            "OPTIONAL MATCH (a:TaxonomyAlias)-[:ALIAS_OF]->(n) "
            "RETURN n.aliases AS aliases, n.normalizedAliases AS normals, count(a) AS alias_nodes",
            parameters={"key": key},
        )
        assert sorted(node["normals"]) == sorted(f"release {i}" for i in range(writers))
        assert len(node["aliases"]) == len(node["normals"]) == writers
        assert node["alias_nodes"] == writers


class TestNeo4jRecordGroupInheritance:
    async def _seed(self, provider: Neo4jProvider, org_id: str, records: int) -> dict[str, str]:
        """A seed group with two nested groups and ``records`` records under it,
        each record with one attachment inheriting from it."""
        ids = {name: f"{name}-{uuid.uuid4().hex[:8]}" for name in ("user", "app", "root", "child", "grandchild")}
        await provider.client.execute_query(
            """
            CREATE (u:User {id: $user, userId: $user, orgId: $org})
            CREATE (app:App {id: $app, name: 'Drive', type: 'DRIVE', orgId: $org, itOrg: $org})
            CREATE (u)-[:USER_APP_RELATION]->(app)
            CREATE (root:RecordGroup {id: $root, orgId: $org, connectorId: $app, isDeleted: false})
            CREATE (child:RecordGroup {id: $child, orgId: $org, connectorId: $app, isDeleted: false})
            CREATE (grandchild:RecordGroup {id: $grandchild, orgId: $org, connectorId: $app, isDeleted: false})
            CREATE (u)-[:PERMISSION {type: 'USER'}]->(root)
            CREATE (child)-[:INHERIT_PERMISSIONS]->(root)
            CREATE (grandchild)-[:INHERIT_PERMISSIONS]->(child)
            """,
            parameters={"org": org_id, **ids},
        )
        await provider.client.execute_query(
            """
            MATCH (root:RecordGroup {id: $root})
            UNWIND range(1, $n) AS i
            CREATE (r:Record {id: $root + '-r' + toString(i), orgId: $org, connectorId: $app})
            CREATE (r)-[:INHERIT_PERMISSIONS]->(root)
            CREATE (a:Record {id: $root + '-a' + toString(i), orgId: $org, connectorId: $app})
            CREATE (a)-[:INHERIT_PERMISSIONS]->(r)
            """,
            parameters={"root": ids["root"], "n": records, "org": org_id, "app": ids["app"]},
        )
        return ids

    @staticmethod
    async def _walk(provider: Neo4jProvider, query: str, parameters: dict[str, Any]) -> dict[str, Any]:
        async with provider.client.driver.session(database="neo4j") as session:
            result = await session.run(f"PROFILE {query}", parameters)
            await result.data()
            summary = await result.consume()
        found: dict[str, Any] = {"db_hits": 0}

        def visit(plan: dict[str, Any]) -> None:
            found["db_hits"] += int(plan.get("dbHits", 0))
            if plan.get("operatorType", "").startswith("VarLengthExpand"):
                found["operator"] = plan["operatorType"]
                found["rows"] = int(plan.get("rows", 0))
            for child in plan.get("children", []):
                visit(child)

        visit(summary.profile)
        return found

    async def test_walk_does_not_continue_through_records(self, neo4j) -> None:
        provider, org_id = neo4j
        ids = await self._seed(provider, org_id, records=3000)
        captured = _capture_queries(provider)

        context = await provider.get_entity_access_context(ids["user"], org_id)

        assert set(context["record_group_ids"]) == {ids["root"], ids["child"], ids["grandchild"]}
        query, parameters = captured[-1]
        current = await self._walk(provider, query, parameters)
        # The walk's own output is just the two nested groups: the RecordGroup
        # predicate prunes the expand instead of filtering 6000 paths after it.
        assert "Pruning" in current["operator"], current
        assert current["rows"] == 2, current
        # The walk as the branch first wrote it: a labelled child and no path
        # predicate, which expands through every record and attachment.
        original = query.replace(
            "MATCH p = (seed)<-[:INHERIT_PERMISSIONS*1..20]-(child)\n"
            "            WHERE all(n IN nodes(p) WHERE n:RecordGroup)\n"
            "            WITH child, record_level_app_ids\n"
            "            WHERE child.orgId",
            "MATCH (seed)<-[:INHERIT_PERMISSIONS*1..20]-(child:RecordGroup)\n"
            "            WHERE child.orgId",
        )
        assert original != query
        before = await self._walk(provider, original, parameters)
        logger.warning("inheritance walk: current=%s original=%s", current, before)
        assert current["db_hits"] * 2 < before["db_hits"], (current, before)

    async def test_walk_cost_follows_direct_children_not_descendants(self, neo4j) -> None:
        """Doubling the attachments under each record must not change the
        walk: only the seed's direct inbound edges are read."""
        provider, org_id = neo4j
        ids = await self._seed(provider, org_id, records=2000)
        await provider.client.execute_query(
            """
            MATCH (a:Record)-[:INHERIT_PERMISSIONS]->(r:Record)-[:INHERIT_PERMISSIONS]->(:RecordGroup {id: $root})
            CREATE (extra:Record {id: a.id + '-x', orgId: $org, connectorId: r.connectorId})
            CREATE (extra)-[:INHERIT_PERMISSIONS]->(a)
            """,
            parameters={"root": ids["root"], "org": org_id},
        )
        captured = _capture_queries(provider)
        await provider.get_entity_access_context(ids["user"], org_id)
        query, parameters = captured[-1]
        with_deeper_records = await self._walk(provider, query, parameters)

        # 2000 direct children at roughly two db hits each; 4000 deeper records untouched.
        assert with_deeper_records["rows"] == 2, with_deeper_records
        assert with_deeper_records["db_hits"] < 2000 * 3, with_deeper_records


# ---------------------------------------------------------------------------
# ArangoDB
# ---------------------------------------------------------------------------


@pytest.fixture
async def arango() -> AsyncIterator[tuple[ArangoHTTPProvider, str]]:
    async with _arango_backend() as backend:
        yield backend


@contextlib.asynccontextmanager
async def _arango_backend() -> AsyncIterator[tuple[ArangoHTTPProvider, str]]:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
        await provider.ensure_schema()
    except Exception as exc:
        pytest.skip(f"ArangoDB not available at {ARANGO_URL}: {exc}")
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org_id
    finally:
        for collection in (TOPICS, CollectionNames.RECORDS.value):
            await provider.http_client.execute_aql(
                f"FOR d IN {collection} FILTER d.orgId == @org REMOVE d IN {collection}",
                {"org": org_id},
            )
        await provider.http_client.execute_aql(
            f"FOR e IN {CollectionNames.BELONGS_TO_TOPIC.value} "
            f"FILTER e.itOrg == @org OR STARTS_WITH(e._from, CONCAT('records/', @org)) "
            f"REMOVE e IN {CollectionNames.BELONGS_TO_TOPIC.value}",
            {"org": org_id},
        )


def _topic(org_id: str, name: str) -> tuple[str, dict[str, Any]]:
    normalized = name.casefold()
    key = taxonomy_node_key(org_id, TOPICS, normalized)
    return key, {"id": key, "name": name, "normalizedName": normalized, "orgId": org_id}


class TestArangoTaxonomyWritesUnderConcurrentTransactions:
    async def test_the_old_in_transaction_insert_conflicts(self, arango) -> None:
        """Documents the failure the fix removes: two open stream transactions
        inserting one deterministic key. Kept so a server change that makes it
        pass is noticed."""
        provider, org_id = arango
        _, node = _topic(org_id, "Onboarding")
        t1 = await provider.begin_transaction(read=[TOPICS], write=[TOPICS])
        t2 = await provider.begin_transaction(read=[TOPICS], write=[TOPICS])
        try:
            await provider.create_taxonomy_node_if_absent(TOPICS, dict(node), transaction=t1)
            with pytest.raises(Exception, match="1200"):
                await provider.create_taxonomy_node_if_absent(TOPICS, dict(node), transaction=t2)
        finally:
            for txn in (t1, t2):
                await provider.commit_transaction(txn)

    async def test_a_no_op_alias_write_takes_no_lock(self, arango) -> None:
        """Popular nodes get alias writes from many records; writing the same
        lists back locked the node for the rest of each transaction."""
        provider, org_id = arango
        key, node = _topic(org_id, "Release")
        await provider.create_taxonomy_node_if_absent(TOPICS, node)
        await provider.add_taxonomy_aliases(TOPICS, key, ["Release notes"], ["release notes"], org_id=org_id)
        t1 = await provider.begin_transaction(read=[TOPICS], write=[TOPICS])
        t2 = await provider.begin_transaction(read=[TOPICS], write=[TOPICS])
        try:
            for txn in (t1, t2):
                await provider.add_taxonomy_aliases(
                    TOPICS, key, ["Release notes"], ["release notes"], transaction=txn, org_id=org_id,
                )
        finally:
            for txn in (t1, t2):
                await provider.commit_transaction(txn)

    async def test_two_records_enriching_the_same_new_topic_both_succeed(self, arango) -> None:
        """The real transformer: each record resolves the same new canonical
        topic and enriches in its own graph transaction, concurrently."""
        provider, org_id = arango
        key, _ = _topic(org_id, "Quarterly planning")
        record_ids = [f"{org_id}-rec-{i}" for i in range(4)]
        await provider.batch_upsert_nodes([
            {
                "id": record_id, "orgId": org_id, "recordName": f"Doc {i}",
                "externalRecordId": record_id, "recordType": "FILE", "origin": "CONNECTOR",
                "connectorId": f"{org_id}-conn", "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            for i, record_id in enumerate(record_ids)
        ], CollectionNames.RECORDS.value)

        def resolution(record_index: int) -> EntityResolution:
            res = EntityResolution(org_id=org_id, mode=ResolutionMode.APPLY)
            res.add(ResolvedEntity(
                kind=TOPIC, key=key, name="Quarterly planning", normalized="quarterly planning",
                is_new=True, decision="new",
                aliases=[f"Q planning {record_index}"], new_aliases=[f"Q planning {record_index}"],
                extracted_names=[f"Q planning {record_index}"],
            ))
            return res

        transformer = GraphDBTransformer(graph_provider=provider, logger=logger)
        metadata = lambda: SemanticMetadata(  # noqa: E731
            categories=[], topics=["Quarterly planning"], languages=[], departments=[],
        )

        results = await asyncio.gather(*(
            transformer.save_metadata_to_db(
                record_id, metadata(), f"vr-{record_id}", resolution=resolution(i),
            )
            for i, record_id in enumerate(record_ids)
        ), return_exceptions=True)

        assert not [r for r in results if isinstance(r, BaseException)], results
        nodes = await provider.http_client.execute_aql(
            f"FOR d IN {TOPICS} FILTER d.orgId == @org RETURN d", {"org": org_id},
        )
        assert [n["_key"] for n in nodes] == [key]
        assert sorted(nodes[0]["normalizedAliases"]) == sorted(f"q planning {i}" for i in range(4))
        edges = await provider.http_client.execute_aql(
            f"FOR e IN {CollectionNames.BELONGS_TO_TOPIC.value} "
            f"FILTER e._to == @to RETURN e._from",
            {"to": f"{TOPICS}/{key}"},
        )
        assert sorted(edges) == sorted(f"records/{r}" for r in record_ids)


# ---------------------------------------------------------------------------
# Both backends
# ---------------------------------------------------------------------------

# How long the in-flight write keeps Neo4j writes queued behind its lock.
HOLD_SECONDS = 1.0


@pytest.fixture(params=["neo4j", "arango"])
async def graph(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    backend = _neo4j_backend(monkeypatch) if request.param == "neo4j" else _arango_backend()
    async with backend as connected:
        yield connected


async def _write_in_flight(
    provider: IGraphDBProvider, node: dict[str, Any], *, alias_update: bool, alias: str,
) -> Callable[[], Awaitable[None]]:
    """Another record's write to ``node``, left uncommitted so it holds the
    node's lock: its create of the node or, with ``alias_update``, its alias
    update of the node it already created. Returns the commit."""
    if alias_update:
        await provider.create_taxonomy_node_if_absent(TOPICS, dict(node))
    if isinstance(provider, ArangoHTTPProvider):
        txn = await provider.begin_transaction(read=[TOPICS], write=[TOPICS])
        if alias_update:
            await provider.add_taxonomy_aliases(
                TOPICS, node["id"], [alias], [alias.casefold()], transaction=txn, org_id=node["orgId"],
            )
        else:
            await provider.create_taxonomy_node_if_absent(TOPICS, dict(node), transaction=txn)
        return lambda: provider.commit_transaction(txn)

    # The provider's own transactions are auto-commit unless
    # NEO4J_EXPLICIT_TRANSACTIONS is set, and those hold no lock.
    label = collection_to_label(TOPICS)
    session = provider.client.driver.session(database="neo4j")
    tx = await session.begin_transaction()
    if alias_update:
        await tx.run(
            f"MATCH (n:{label} {{id: $id}}) SET n.aliases = [$alias], n.normalizedAliases = [$normalized]",
            id=node["id"], alias=alias, normalized=alias.casefold(),
        )
    else:
        props = {k: v for k, v in node.items() if k != "id"}
        await tx.run(f"MERGE (n:{label} {{id: $id}}) ON CREATE SET n += $props", id=node["id"], props=props)

    async def commit() -> None:
        try:
            await tx.commit()
        finally:
            await session.close()

    return commit


class _WriteDuringWrite:
    """Sends the record's writes to the topic while another writer holds it,
    and releases that writer once one of them fails (ArangoDB refuses a locked
    key at once) or ``HOLD_SECONDS`` after the first was sent (Neo4j queues
    them behind the lock)."""

    def __init__(self, release: Callable[[], Awaitable[None]]) -> None:
        self._release = release
        self._released = False
        self._in_flight = 0
        self._lock = asyncio.Lock()
        self.sent = asyncio.Event()
        self.first_error: BaseException | None = None
        self.queued_behind_lock = False

    async def release(self) -> None:
        async with self._lock:
            if not self._released:
                self._released = True
                self.queued_behind_lock = self._in_flight > 0
                await self._release()

    async def release_after_hold(self) -> None:
        await self.sent.wait()
        await asyncio.sleep(HOLD_SECONDS)
        await self.release()

    def install(self, provider: IGraphDBProvider) -> None:
        if isinstance(provider, ArangoHTTPProvider):
            owner, name = provider.http_client, "batch_insert_documents"

            def is_topic_write(collection: str, *_: object, txn_id: str | None = None, **__: object) -> bool:
                return collection == TOPICS and txn_id is None
        else:
            # MERGE matches an existing node without locking it, so with the
            # node already created it is the alias update that queues.
            owner, name = provider.client, "execute_query"
            label = collection_to_label(TOPICS)
            writes = (f"MERGE (n:{label} {{id: $id}})", f"MATCH (n:{label} {{id: $key}})")

            def is_topic_write(query: str, *_: object, txn_id: str | None = None, **__: object) -> bool:
                return txn_id is None and any(write in query for write in writes)

        real = getattr(owner, name)

        async def watched(*args: object, **kwargs: object) -> object:
            if self._released or not is_topic_write(*args, **kwargs):
                return await real(*args, **kwargs)
            self.sent.set()
            self._in_flight += 1
            try:
                return await real(*args, **kwargs)
            except Exception as exc:
                self.first_error = self.first_error or exc
                raise
            finally:
                self._in_flight -= 1
                if self.first_error is not None:
                    await self.release()

        setattr(owner, name, watched)


class TestConcurrentEnrichmentOfOneNewTopic:
    @pytest.mark.parametrize("alias_update", [False, True], ids=["during-its-create", "during-its-alias-update"])
    async def test_a_record_creating_a_topic_another_record_is_writing_succeeds(
        self, graph, alias_update: bool,
    ) -> None:
        """Two records resolve the same new topic and enrich at the same
        moment: the second record's writes to the topic arrive while the
        first record's write to it is still in flight. Both enrichments
        complete, with one topic node linked from both records."""
        provider, org_id = graph
        key, node = _topic(org_id, "Quarterly planning")
        record_ids = [f"{org_id}-rec-{i}" for i in range(2)]
        await provider.batch_upsert_nodes([
            {
                "id": record_id, "orgId": org_id, "recordName": f"Doc {i}",
                "externalRecordId": record_id, "recordType": "FILE", "origin": "CONNECTOR",
                "connectorId": f"{org_id}-conn", "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            for i, record_id in enumerate(record_ids)
        ], CollectionNames.RECORDS.value)

        def resolution(record_index: int) -> EntityResolution:
            res = EntityResolution(org_id=org_id, mode=ResolutionMode.APPLY)
            res.add(ResolvedEntity(
                kind=TOPIC, key=key, name="Quarterly planning", normalized="quarterly planning",
                is_new=True, decision="new",
                aliases=[f"Q planning {record_index}"], new_aliases=[f"Q planning {record_index}"],
                extracted_names=[f"Q planning {record_index}"],
            ))
            return res

        transformer = GraphDBTransformer(graph_provider=provider, logger=logger)

        async def enrich(record_index: int) -> list[Any]:
            return await transformer.save_metadata_to_db(
                record_ids[record_index],
                SemanticMetadata(categories=[], topics=["Quarterly planning"], languages=[], departments=[]),
                f"vr-{record_ids[record_index]}",
                resolution=resolution(record_index),
            )

        overlap = _WriteDuringWrite(
            await _write_in_flight(provider, node, alias_update=alias_update, alias="Q planning 0"),
        )
        overlap.install(provider)
        try:
            second, _ = await asyncio.gather(
                enrich(1), overlap.release_after_hold(), return_exceptions=True,
            )
        finally:
            await overlap.release()
        first = await enrich(0)

        # The second record's write really ran into the first one's.
        if isinstance(provider, ArangoHTTPProvider):
            assert "1200" in str(overlap.first_error), overlap.first_error
        else:
            assert overlap.first_error is None and overlap.queued_behind_lock
        assert not isinstance(second, BaseException), second
        assert [e.entity_id for e in first] == [key] and [e.entity_id for e in second] == [key]

        nodes = await provider.get_nodes_by_filters(TOPICS, {"orgId": org_id})
        assert [GraphDBTransformer._node_key(n) for n in nodes] == [key]
        assert sorted(nodes[0]["normalizedAliases"]) == ["q planning 0", "q planning 1"]
        for record_id in record_ids:
            edges = await provider.get_edges_from_node_with_target_name(
                f"{CollectionNames.RECORDS.value}/{record_id}", CollectionNames.BELONGS_TO_TOPIC.value,
            )
            assert [e["_to"] for e in edges] == [f"{TOPICS}/{key}"]
            record = await provider.get_document(record_id, CollectionNames.RECORDS.value)
            assert record["extractionStatus"] == "COMPLETED"


class TestAliasWritesAreOrgScoped:
    """An org's spellings never land on a legacy node all orgs share, or on
    another org's node (KG-25)."""

    async def test_neo4j(self, neo4j) -> None:
        provider, org_id = neo4j
        mine, theirs, legacy = (f"{org_id}-{n}" for n in ("mine", "theirs", "legacy"))
        await provider.client.execute_query(
            "CREATE (:Topics {id: $mine, name: 'A', normalizedName: 'a', orgId: $org}) "
            "CREATE (:Topics {id: $theirs, name: 'A', normalizedName: 'a', orgId: $org + '-other'}) "
            "CREATE (:Topics {id: $legacy, name: 'A', itOrg: $org})",
            parameters={"mine": mine, "theirs": theirs, "legacy": legacy, "org": org_id},
        )
        try:
            for key in (mine, theirs, legacy):
                await provider.add_taxonomy_aliases(TOPICS, key, ["Alpha"], ["alpha"], org_id=org_id)
            rows = await provider.client.execute_query(
                "MATCH (n:Topics) WHERE n.id IN $ids RETURN n.id AS id, n.aliases AS aliases",
                parameters={"ids": [mine, theirs, legacy]},
            )
            aliases = {r["id"]: r["aliases"] for r in rows}
            assert aliases == {mine: ["Alpha"], theirs: None, legacy: None}
        finally:
            await provider.client.execute_query(
                "MATCH (n:Topics) WHERE n.id IN $ids DETACH DELETE n",
                parameters={"ids": [theirs, legacy]},
            )

    async def test_arango(self, arango) -> None:
        provider, org_id = arango
        mine, theirs, legacy = (f"{org_id}-{n}" for n in ("mine", "theirs", "legacy"))
        await provider.http_client.execute_aql(
            f"FOR d IN @docs INSERT d INTO {TOPICS}",
            {"docs": [
                {"_key": mine, "name": "A", "normalizedName": "a", "orgId": org_id},
                {"_key": theirs, "name": "A", "normalizedName": "a", "orgId": f"{org_id}-other"},
                {"_key": legacy, "name": "A"},
            ]},
        )
        try:
            for key in (mine, theirs, legacy):
                await provider.add_taxonomy_aliases(TOPICS, key, ["Alpha"], ["alpha"], org_id=org_id)
            rows = await provider.http_client.execute_aql(
                f"FOR d IN {TOPICS} FILTER d._key IN @keys RETURN {{k: d._key, a: d.aliases}}",
                {"keys": [mine, theirs, legacy]},
            )
            assert {r["k"]: r["a"] for r in rows} == {mine: ["Alpha"], theirs: None, legacy: None}
        finally:
            await provider.http_client.execute_aql(
                f"FOR d IN {TOPICS} FILTER d._key IN @keys REMOVE d IN {TOPICS}",
                {"keys": [theirs, legacy]},
            )


async def _assert_fenced_clear(provider: Neo4jProvider | ArangoHTTPProvider, key: str) -> None:
    """The flag clear is fenced on the due time the sweep read: a stale fence
    changes nothing, the current one merges (other fields are kept), and the
    attempt counter fits ArangoDB's strict records schema."""
    records = CollectionNames.RECORDS.value
    cleared = {"duplicateReconcilePending": False, "duplicateReconcileAttempts": 2,
               "duplicateReconcileDueAt": None}
    stale = {"duplicateReconcilePending": True, "duplicateReconcileDueAt": 999}
    assert await provider.update_node_fields_if_match(key, records, cleared, stale) is False
    current = {"duplicateReconcilePending": True, "duplicateReconcileDueAt": 1000}
    assert await provider.update_node_fields_if_match(key, records, cleared, current) is True
    doc = await provider.get_document(key, records)
    assert doc["duplicateReconcilePending"] is False
    assert doc["duplicateReconcileAttempts"] == 2
    assert doc.get("duplicateReconcileDueAt") is None
    assert doc["orgId"]  # merged, not replaced
    absent = {"duplicateReconcileDueAt": None, "duplicateReconcilePending": False}
    assert await provider.update_node_fields_if_match(
        key, records, {"duplicateReconcileAttempts": 3}, absent,
    ) is True


class TestPendingDuplicateReconcile:
    """The retry sweep's query, and the attempt counter on ArangoDB's strict
    records schema (KG-51)."""

    async def test_neo4j(self, neo4j) -> None:
        provider, org_id = neo4j
        await provider.client.execute_query(
            "UNWIND $rows AS row CREATE (r:Record) SET r = row",
            parameters={"rows": [
                {"id": f"{org_id}-old", "orgId": org_id, "duplicateReconcilePending": True,
                 "duplicateReconcileDueAt": 1000},
                {"id": f"{org_id}-new", "orgId": org_id, "duplicateReconcilePending": True,
                 "duplicateReconcileDueAt": 9000},
                {"id": f"{org_id}-done", "orgId": org_id, "duplicateReconcilePending": False,
                 "duplicateReconcileDueAt": 1000},
            ]},
        )
        rows = await provider.get_records_pending_duplicate_reconcile(due_before_ms=5000, limit=100)
        mine = [r for r in rows if r["_key"].startswith(org_id)]
        assert mine == [{"_key": f"{org_id}-old", "duplicateReconcileAttempts": None,
                         "duplicateReconcileDueAt": 1000}]
        await _assert_fenced_clear(provider, f"{org_id}-old")

    async def test_arango(self, arango) -> None:
        provider, org_id = arango
        from app.config.constants.arangodb import Connectors, OriginTypes
        from app.models.entities import Record, RecordType

        def _record(suffix: str, **fields: object) -> dict[str, Any]:
            doc = Record(
                id=f"{org_id}-{suffix}", org_id=org_id, record_name="doc", record_type=RecordType.FILE,
                external_record_id=f"ext-{suffix}", version=0, origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.KNOWLEDGE_BASE, connector_id="c-it",
            ).to_arango_base_record()
            return {**doc, **fields}

        docs = [
            _record("old", duplicateReconcilePending=True, duplicateReconcileDueAt=1000),
            _record("new", duplicateReconcilePending=True, duplicateReconcileDueAt=9000),
            _record("done", duplicateReconcilePending=False, duplicateReconcileDueAt=1000),
        ]
        await provider.http_client.execute_aql(
            f"FOR d IN @docs INSERT d INTO {CollectionNames.RECORDS.value}", {"docs": docs},
        )
        rows = await provider.get_records_pending_duplicate_reconcile(due_before_ms=5000, limit=100)
        mine = [r for r in rows if r["_key"].startswith(org_id)]
        assert mine == [{"_key": f"{org_id}-old", "duplicateReconcileAttempts": None,
                         "duplicateReconcileDueAt": 1000}]
        await _assert_fenced_clear(provider, f"{org_id}-old")


class TestDuplicatePathRowsCarryTheNodesOrg:
    async def test_neo4j(self, neo4j) -> None:
        provider, org_id = neo4j
        rec, mine, legacy = f"{org_id}-rec", f"{org_id}-t-mine", f"{org_id}-t-legacy"
        await provider.client.execute_query(
            "CREATE (r:Record {id: $rec, orgId: $org}) "
            "CREATE (a:Topics {id: $mine, name: 'Mine', orgId: $org}) "
            "CREATE (b:Topics {id: $legacy, name: 'Legacy', itOrg: $org}) "
            "CREATE (r)-[:BELONGS_TO_TOPIC]->(a) CREATE (r)-[:BELONGS_TO_TOPIC]->(b)",
            parameters={"rec": rec, "mine": mine, "legacy": legacy, "org": org_id},
        )
        try:
            rows = await provider.get_taxonomy_entities_for_record(rec)
            assert {r["entityId"]: r["orgId"] for r in rows} == {mine: org_id, legacy: None}
        finally:
            await provider.client.execute_query(
                "MATCH (n:Topics {id: $legacy}) DETACH DELETE n", parameters={"legacy": legacy},
            )

    async def test_arango(self, arango) -> None:
        provider, org_id = arango
        mine, legacy = f"{org_id}-t-mine", f"{org_id}-t-legacy"
        await provider.http_client.execute_aql(
            f"FOR d IN @docs INSERT d INTO {TOPICS}",
            {"docs": [{"_key": mine, "name": "Mine", "orgId": org_id}, {"_key": legacy, "name": "Legacy"}]},
        )
        rec = f"{org_id}-rec"
        await provider.http_client.execute_aql(
            f"FOR t IN @targets INSERT {{_from: CONCAT('records/', @rec), _to: CONCAT('{TOPICS}/', t), "
            f"createdAtTimestamp: 1}} INTO {CollectionNames.BELONGS_TO_TOPIC.value}",
            {"targets": [mine, legacy], "rec": rec},
        )
        try:
            rows = await provider.get_taxonomy_entities_for_record(rec)
            assert {r["entityId"]: r["orgId"] for r in rows} == {mine: org_id, legacy: None}
        finally:
            await provider.http_client.execute_aql(
                f"FOR d IN {TOPICS} FILTER d._key == @k REMOVE d IN {TOPICS}", {"k": legacy},
            )


class TestNeo4jLegacyAliasHeal:
    """KG-50: aliases stored as node lists before TaxonomyAlias nodes existed
    are healed once at startup, so tier 0 matches them again."""

    # A deadlocked heal (the batches waiting on a lock the outer query holds)
    # hangs for ever; fail fast instead.
    @pytest.mark.timeout(60)
    async def test_list_only_aliases_get_alias_nodes_once(self, neo4j) -> None:
        provider, org_id = neo4j
        key = f"{org_id}-legacy-alias"
        await provider.client.execute_query(
            "CREATE (:Topics {id: $key, name: 'Release', normalizedName: 'release', orgId: $org, "
            "aliases: ['Go live'], normalizedAliases: ['go live']})",
            parameters={"key": key, "org": org_id},
        )
        await provider.client.execute_query(
            "MATCH (m:SchemaMigration {id: 'taxonomy_alias_nodes_v1'}) DELETE m",
        )
        assert await provider.find_taxonomy_nodes(TOPICS, org_id, ["go live"]) == []

        assert await provider.heal_taxonomy_alias_nodes() > 0
        rows = await provider.find_taxonomy_nodes(TOPICS, org_id, ["go live"])
        assert [r["id"] for r in rows] == [key]
        assert await provider.heal_taxonomy_alias_nodes() == 0
        await provider.client.execute_query(
            "MATCH (a:TaxonomyAlias {orgId: $org}) DETACH DELETE a", parameters={"org": org_id},
        )


class TestRecordPeopleEdges:
    """KG-13: the new person edge types pass the ArangoDB collection schema
    (strict, enum of edge types) and are written once per type on both."""

    async def test_neo4j(self, neo4j) -> None:
        provider, org_id = neo4j
        await provider.client.execute_query(
            "CREATE (:Record {id: $rec, orgId: $org}) CREATE (:User {id: $user, orgId: $org})",
            parameters={"rec": f"{org_id}-rec", "user": f"{org_id}-user", "org": org_id},
        )
        edges = [
            {"_from": f"records/{org_id}-rec", "_to": f"users/{org_id}-user", "edgeType": t, "createdAtTimestamp": 1}
            for t in ("AUTHORED_BY", "ADDRESSED_TO", "AUTHORED_BY")
        ]
        await provider.batch_create_entity_relations(edges)
        rows = await provider.client.execute_query(
            "MATCH (:Record {id: $rec})-[r]->(:User {id: $user}) RETURN r.edgeType AS t ORDER BY t",
            parameters={"rec": f"{org_id}-rec", "user": f"{org_id}-user"},
        )
        assert [r["t"] for r in rows] == ["ADDRESSED_TO", "AUTHORED_BY"]

    async def test_arango(self, arango) -> None:
        provider, org_id = arango
        edges = [
            {"_from": f"records/{org_id}-rec", "_to": f"users/{org_id}-user", "edgeType": t,
             "createdAtTimestamp": 1, "itOrg": org_id}
            for t in ("AUTHORED_BY", "ADDRESSED_TO", "REVIEWED_BY", "OWNED_BY", "AUTHORED_BY")
        ]
        try:
            await provider.batch_create_entity_relations(edges)
            rows = await provider.http_client.execute_aql(
                "FOR e IN entityRelations FILTER e.itOrg == @org SORT e.edgeType RETURN e.edgeType", {"org": org_id},
            )
            assert rows == ["ADDRESSED_TO", "AUTHORED_BY", "OWNED_BY", "REVIEWED_BY"]
        finally:
            await provider.http_client.execute_aql(
                "FOR e IN entityRelations FILTER e.itOrg == @org REMOVE e IN entityRelations", {"org": org_id},
            )
