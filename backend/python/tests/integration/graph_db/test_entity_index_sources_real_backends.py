"""Against real servers: the entity index rebuild's graph reads and state
writes on Neo4j 5.26 and ArangoDB 3.12. The unit tests pin query text; only
a server shows the queries parse, page in key order, honour the scope, and
that ArangoDB's strict app and org schemas accept the rebuild's state fields.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_entity_index_sources_real_backends.py -m integration
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    ConnectorScopes,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import Record, RecordGroupType, RecordType
from app.modules.indexing.entity_index_rebuild import EntityIndexState
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

APPS = CollectionNames.APPS.value
ORGS = CollectionNames.ORGS.value
RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
TOPICS = CollectionNames.TOPICS.value
DEPARTMENTS = CollectionNames.DEPARTMENTS.value

logger = logging.getLogger("entity-index-it")


class _Neo4j:
    def __init__(self, provider: Neo4jProvider) -> None:
        self.provider = provider

    async def run(self, query: str, **params: object) -> None:
        await self.provider.client.execute_query(query, parameters=params)

    async def seed(self, s: dict[str, str]) -> None:
        await self.run(
            """
            CREATE (:App {id: $app, name: 'Drive', status: 'ACTIVE', testRun: $run})
            CREATE (:App {id: $deleting, name: 'Old', status: 'DELETING', testRun: $run})
            CREATE (:Organization {id: $org, accountType: 'enterprise', isActive: true, testRun: $run})
            CREATE (:RecordGroup {id: $g1, groupName: 'Eng', orgId: $org, connectorId: $app})
            CREATE (:RecordGroup {id: $g2, name: 'Ops', orgId: $org, connectorId: $app})
            CREATE (:Topics {id: $t1, name: 'Pricing', normalizedName: 'pricing', orgId: $org,
                             aliases: ['price model']})
            CREATE (:Topics {id: $t2, name: 'Billing', normalizedName: 'billing', orgId: $org})
            CREATE (:Topics {id: $t_legacy, name: 'Legacy', testRun: $run})
            CREATE (:Topics {id: $t_other, name: 'Other', normalizedName: 'other', orgId: $other})
            CREATE (:Departments {id: $d_org, departmentName: 'Finance', orgId: $org})
            CREATE (:Departments {id: $d_global, departmentName: 'Legal', testRun: $run})
            """,
            **s,
        )
        for i in (1, 2, 3):
            await self.run(
                """
                CREATE (:Record {id: $id, orgId: $org, connectorId: $app, recordName: $name,
                                 recordGroupId: $g1, indexingStatus: 'COMPLETED', isDeleted: false})
                """,
                id=s[f"r{i}"], org=s["org"], app=s["app"], name=f"doc {i}", g1=s["g1"],
            )

    async def clean(self, s: dict[str, str]) -> None:
        await self.run(
            "MATCH (n) WHERE n.orgId IN [$org, $other] OR n.testRun = $run DETACH DELETE n",
            org=s["org"], other=s["other"], run=s["run"],
        )

    async def mark_all(self, marker: str, swept_at: int) -> None:
        await self.run("MATCH (n:App) SET n.entityIndexState = $m", m=marker)
        await self.run(
            "MATCH (n:Organization) SET n.entityIndexState = $m, n.entityIndexSweptAt = $t",
            m=marker, t=swept_at,
        )


class _Arango:
    def __init__(self, provider: ArangoHTTPProvider) -> None:
        self.provider = provider

    async def aql(self, query: str, **binds: object) -> list:
        return await self.provider.http_client.execute_aql(query, binds)

    async def seed(self, s: dict[str, str]) -> None:
        app = {
            "type": "DRIVE", "appGroup": "Google Workspace", "scope": ConnectorScopes.TEAM.value,
            "isActive": True, "createdAtTimestamp": 1,
        }
        await self.aql(
            f"FOR a IN @apps INSERT a INTO {APPS}",
            apps=[{**app, "_key": s["app"], "name": "Drive", "status": "ACTIVE"},
                  {**app, "_key": s["deleting"], "name": "Old", "status": "DELETING"}],
        )
        await self.aql(
            f"INSERT @o INTO {ORGS}",
            o={"_key": s["org"], "accountType": "enterprise", "isActive": True},
        )
        group = {
            "groupType": RecordGroupType.KB.value, "connectorName": Connectors.KNOWLEDGE_BASE.value,
            "createdAtTimestamp": 1, "orgId": s["org"], "connectorId": s["app"],
        }
        await self.aql(
            f"FOR g IN @groups INSERT g INTO {GROUPS}",
            groups=[{**group, "_key": s["g1"], "groupName": "Eng"},
                    {**group, "_key": s["g2"], "groupName": "Ops"}],
        )
        for key, name, org in ((s["t1"], "Pricing", s["org"]), (s["t2"], "Billing", s["org"]),
                               (s["t_other"], "Other", s["other"])):
            await self.provider.create_taxonomy_node_if_absent(TOPICS, {
                "id": key, "name": name, "normalizedName": name.lower(), "orgId": org,
            })
        await self.provider.add_taxonomy_aliases(
            TOPICS, s["t1"], ["price model"], ["price model"], org_id=s["org"],
        )
        await self.aql(f"INSERT @t INTO {TOPICS}", t={"_key": s["t_legacy"], "name": "Legacy"})
        await self.aql(
            f"FOR d IN @depts INSERT d INTO {DEPARTMENTS}",
            depts=[{"_key": s["d_org"], "departmentName": "Finance", "orgId": s["org"]},
                   {"_key": s["d_global"], "departmentName": "Legal", "orgId": None}],
        )
        docs = [
            Record(
                id=s[f"r{i}"], org_id=s["org"], record_name=f"doc {i}", record_type=RecordType.FILE,
                external_record_id=f"ext-{i}", version=0, origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.KNOWLEDGE_BASE, connector_id=s["app"],
                record_group_id=s["g1"], indexing_status=ProgressStatus.COMPLETED.value,
            ).to_arango_base_record()
            for i in (1, 2, 3)
        ]
        await self.aql(f"FOR d IN @docs INSERT d INTO {RECORDS}", docs=docs)

    async def clean(self, s: dict[str, str]) -> None:
        keys = [v for k, v in s.items() if k not in ("run", "other")]
        for collection in (APPS, ORGS, GROUPS, TOPICS, DEPARTMENTS, RECORDS):
            await self.aql(
                f"FOR d IN {collection} FILTER d._key IN @keys REMOVE d IN {collection}", keys=keys,
            )

    async def mark_all(self, marker: str, swept_at: int) -> None:
        await self.aql(f"FOR d IN {APPS} UPDATE d WITH {{entityIndexState: @m}} IN {APPS}", m=marker)
        await self.aql(
            f"FOR d IN {ORGS} UPDATE d WITH {{entityIndexState: @m, entityIndexSweptAt: @t}} IN {ORGS}",
            m=marker, t=swept_at,
        )


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
) -> AsyncIterator[tuple[Any, Any, dict[str, str]]]:
    open_, helper = {"neo4j": (_open_neo4j, _Neo4j), "arango": (_open_arango, _Arango)}[request.param]
    try:
        provider = await open_(monkeypatch)
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    run = uuid.uuid4().hex[:10]
    names = ("app", "deleting", "org", "other", "g1", "g2", "t1", "t2", "t_legacy", "t_other",
             "d_org", "d_global", "r1", "r2", "r3")
    seeded = {"run": run, **{n: f"{n}-{run}" for n in names}}
    db = helper(provider)
    await db.seed(seeded)
    try:
        yield provider, db, seeded
    finally:
        await db.clean(seeded)
        if request.param == "neo4j":
            await provider.disconnect()


async def _page_all(provider: Neo4jProvider | ArangoHTTPProvider, source: str, scope: str, limit: int) -> list[list[str]]:
    pages, after = [], None
    while True:
        rows = await provider.page_entity_index_source(source, scope, after, limit)
        pages.append([r["_key"] for r in rows])
        if len(rows) < limit:
            return pages
        after = rows[-1]["_key"]


class TestSources:
    async def test_records_page_in_key_order_with_their_fields(self, backend) -> None:
        provider, _, s = backend
        assert await _page_all(provider, RECORDS, s["app"], 2) == [[s["r1"], s["r2"]], [s["r3"]]]
        (row, *_) = await provider.page_entity_index_source(RECORDS, s["app"], None, 1)
        assert row["name"] == "doc 1" and row["orgId"] == s["org"]
        assert row["recordGroupId"] == s["g1"]
        assert row["indexingStatus"] == "COMPLETED" and not row["isDeleted"]

    async def test_record_groups_of_the_connector(self, backend) -> None:
        provider, _, s = backend
        rows = await provider.page_entity_index_source(GROUPS, s["app"], None, 10)
        assert [(r["_key"], r["name"], r["orgId"]) for r in rows] == [
            (s["g1"], "Eng", s["org"]), (s["g2"], "Ops", s["org"]),
        ]

    async def test_topics_are_the_orgs_canonical_nodes_only(self, backend) -> None:
        provider, _, s = backend
        rows = await provider.page_entity_index_source(TOPICS, s["org"], None, 10)
        by_key = {r["_key"]: r for r in rows}
        assert set(by_key) == {s["t1"], s["t2"]}
        assert by_key[s["t1"]]["aliases"] == ["price model"]
        assert by_key[s["t2"]]["aliases"] == []

    async def test_departments_include_global_ones(self, backend) -> None:
        provider, _, s = backend
        rows = await provider.page_entity_index_source(DEPARTMENTS, s["org"], None, 1000)
        names = {r["_key"]: r["name"] for r in rows}
        assert names[s["d_org"]] == "Finance" and names[s["d_global"]] == "Legal"

    async def test_after_key_past_the_end_is_empty(self, backend) -> None:
        provider, _, s = backend
        assert await provider.page_entity_index_source(RECORDS, s["app"], s["r3"], 10) == []


class TestCandidatesAndState:
    async def test_app_candidate_skips_current_and_deleting(self, backend) -> None:
        provider, db, s = backend
        marker = f"v1:it-{s['run']}"
        await db.mark_all(marker, swept_at=10**15)
        assert await provider.get_entity_index_candidate(APPS, marker) is None
        await provider.update_node(s["app"], APPS, {EntityIndexState.STATE: None})
        await provider.update_node(s["deleting"], APPS, {EntityIndexState.STATE: None})
        found = await provider.get_entity_index_candidate(APPS, marker)
        assert found["_key"] == s["app"]

    async def test_org_candidate_by_marker_or_due_sweep(self, backend) -> None:
        provider, db, s = backend
        marker = f"v1:it-{s['run']}"
        await db.mark_all(marker, swept_at=10**15)
        assert await provider.get_entity_index_candidate(ORGS, marker, sweep_before=10**14) is None
        await provider.update_node(s["org"], ORGS, {EntityIndexState.SWEPT_AT: 5})
        found = await provider.get_entity_index_candidate(ORGS, marker, sweep_before=10**14)
        assert found["_key"] == s["org"]
        assert await provider.get_entity_index_candidate(ORGS, marker) is None

    async def test_every_state_field_is_accepted(self, backend) -> None:
        """ArangoDB's app and org schemas are strict: an unlisted field fails
        the write and the rebuild could never record progress."""
        provider, _, s = backend
        common = {
            EntityIndexState.STATE: "v1:x", EntityIndexState.PHASE: RECORDS,
            EntityIndexState.AFTER_KEY: "k", EntityIndexState.ATTEMPTS: 1,
            EntityIndexState.FAILURES: 2, EntityIndexState.EXHAUSTED: False,
            EntityIndexState.TARGET: "v1:y", EntityIndexState.ERRORS: 0,
        }
        assert await provider.update_node(s["app"], APPS, common)
        assert await provider.update_node(s["org"], ORGS, common | {
            EntityIndexState.SWEPT_AT: 1_800_000_000_000, EntityIndexState.SWEEP_OFFSET: "o",
            EntityIndexState.SWEEP_FAILURES: 1,
        })
        assert await provider.update_node(s["app"], APPS, dict.fromkeys(common))
