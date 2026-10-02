"""Against real servers (KG-13 slice 3a): Salesforce accounts as organisation
entities. A record in an account's record group links to the account's
organisation (INFERRED); the organisation is found only through records the
user can read, and only within its tenant. On Neo4j 5.26 and ArangoDB 3.12.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_record_organizations_real_backends.py -m integration
"""
from __future__ import annotations

import io
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import DealRecord, RecordGroupType, RecordType, TicketRecord
from app.scripts.kg_record_people import backfill
from tests.integration.graph_db.test_record_graph_write_concurrency import (
    _open_arango,
    _open_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
logger = logging.getLogger("record-organizations-it")

ORGS = CollectionNames.ORGS.value
GROUPS = CollectionNames.RECORD_GROUPS.value


@pytest.fixture(params=["arango", "neo4j"])
async def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[Any, str]]:
    try:
        provider = await (_open_arango() if request.param == "arango" else _open_neo4j(monkeypatch, explicit=False))
    except Exception as exc:
        pytest.skip(f"{request.param} not available: {exc}")
    org = f"org-it-{uuid.uuid4().hex[:10]}"
    try:
        yield provider, org
    finally:
        await _cleanup(provider, org)


async def _cleanup(provider: Any, org: str) -> None:  # noqa: ANN401
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    if isinstance(provider, Neo4jProvider):
        await provider.client.execute_query(
            "MATCH (n) WHERE n.orgId STARTS WITH $org OR n.id STARTS WITH $org DETACH DELETE n",
            parameters={"org": org},
        )
        await provider.disconnect()
        return
    aql = provider.http_client.execute_aql
    for edges in (
        CollectionNames.ENTITY_RELATIONS.value, CollectionNames.IS_OF_TYPE.value,
        CollectionNames.DEAL_OF.value, CollectionNames.PROSPECT.value,
    ):
        await aql(
            f"FOR e IN {edges} FILTER CONTAINS(e._from, @org) OR CONTAINS(e._to, @org) REMOVE e IN {edges}",
            {"org": org},
        )
    for docs in (
        CollectionNames.RECORDS.value, CollectionNames.DEALS.value, CollectionNames.TICKETS.value,
        CollectionNames.USERS.value, GROUPS, ORGS,
    ):
        await aql(f"FOR d IN {docs} FILTER STARTS_WITH(d._key, @org) REMOVE d IN {docs}", {"org": org})


def _account(org: str, key: str, name: str, *, parent: str | None) -> dict:
    doc = {"id": f"{org}-{key}", "name": name, "accountType": "enterprise", "isActive": True, "isExternal": True}
    if parent is not None:
        doc["parentOrgId"] = parent
    return doc


def _group(org: str, key: str, account_id: str) -> dict:
    return {
        "id": f"{org}-{key}", "groupName": key, "groupType": RecordGroupType.SALESFORCE_ORG.value,
        "connectorName": Connectors.SALESFORCE.value, "connectorId": f"{org}-conn", "orgId": org,
        "externalGroupId": account_id, "createdAtTimestamp": 1,
    }


def _base(org: str, key: str, group: str) -> dict:
    return {
        "id": f"{org}-{key}", "org_id": org, "external_record_id": f"{org}-{key}",
        "origin": OriginTypes.CONNECTOR, "connector_name": Connectors.SALESFORCE,
        "connector_id": f"{org}-conn", "version": 1, "source_created_at": 1000, "source_updated_at": 1000,
        "indexing_status": "COMPLETED", "record_group_id": f"{org}-{group}",
    }


async def _seed(provider: Any, org: str) -> None:  # noqa: ANN401
    await provider.batch_upsert_nodes(
        [{"id": org, "accountType": "enterprise", "isActive": True, "entityIndexState": "v3:fp"}], ORGS,
    )
    await provider.batch_upsert_nodes([
        _account(org, "acme", "Acme", parent=org),
        # Written before accounts carried their tenant: stamped by the backfill
        # from the tenant's prospect edge.
        _account(org, "legacy", "Legacy Co", parent=None),
        # Another tenant's account, wrongly reachable from this org's group.
        _account(org, "other", "Other Inc", parent=f"{org}-x"),
    ], ORGS)
    await provider.batch_upsert_nodes([
        _group(org, "rg-acme", "001A"), _group(org, "rg-legacy", "001L"), _group(org, "rg-other", "001O"),
    ], GROUPS)
    await provider.batch_create_edges([
        {"from_id": f"{org}-rg-{a}", "from_collection": GROUPS, "to_id": f"{org}-{a}", "to_collection": ORGS,
         "createdAtTimestamp": 1}
        for a in ("acme", "legacy", "other")
    ], CollectionNames.DEAL_OF.value)
    await provider.batch_create_edges([{
        "from_id": org, "from_collection": ORGS, "to_id": f"{org}-legacy", "to_collection": ORGS,
        "createdAtTimestamp": 1, "externalId": "001L",
    }], CollectionNames.PROSPECT.value)
    await provider.batch_upsert_nodes([
        {"id": f"{org}-bob", "userId": f"{org}-bob", "orgId": org, "email": f"bob@{org}.test", "fullName": "Bob"},
    ], CollectionNames.USERS.value)
    await provider.batch_upsert_records([
        DealRecord(
            record_name="Acme renewal", record_type=RecordType.DEAL, record_group_type=RecordGroupType.DEAL,
            external_record_group_id="001A", **_base(org, "deal", "rg-acme"),
        ),
        TicketRecord(
            record_name="Acme outage", record_type=RecordType.CASE, record_group_type=RecordGroupType.CASE,
            external_record_group_id="001A", assignee_email=f"bob@{org}.test", **_base(org, "case", "rg-acme"),
        ),
        DealRecord(
            record_name="Legacy deal", record_type=RecordType.DEAL, record_group_type=RecordGroupType.DEAL,
            external_record_group_id="001L", **_base(org, "deal-legacy", "rg-legacy"),
        ),
        DealRecord(
            record_name="Other deal", record_type=RecordType.DEAL, record_group_type=RecordGroupType.DEAL,
            external_record_group_id="001O", **_base(org, "deal-other", "rg-other"),
        ),
    ])


async def _edges_from(provider: Any, record_id: str) -> list[tuple[str, str, str | None]]:  # noqa: ANN401
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    if isinstance(provider, Neo4jProvider):
        rows = await provider.client.execute_query(
            "MATCH (:Record {id: $id})-[r]->(n) WHERE r.edgeType IS NOT NULL "
            "RETURN n.id AS n, r.edgeType AS t, r.origin AS o",
            parameters={"id": record_id},
        )
        return sorted((r["n"], r["t"], r["o"]) for r in rows)
    rows = await provider.http_client.execute_aql(
        "FOR e IN entityRelations FILTER e._from == @f RETURN [PARSE_IDENTIFIER(e._to).key, e.edgeType, e.origin]",
        {"f": f"records/{record_id}"},
    )
    return sorted(tuple(r) for r in rows)


async def test_records_link_to_their_account_and_people_links_survive(backend) -> None:
    provider, org = backend
    await provider.ensure_schema()
    await _seed(provider, org)

    for _ in range(2):
        code = await backfill(provider, GraphDataStore(logger, provider), org, apply=True, logger=logger, out=io.StringIO())
        assert code == 0
        assert await _edges_from(provider, f"{org}-case") == sorted([
            (f"{org}-acme", "FOR_ACCOUNT", "INFERRED"), (f"{org}-bob", "ASSIGNED_TO", "INFERRED"),
        ])
        assert await _edges_from(provider, f"{org}-deal") == [(f"{org}-acme", "FOR_ACCOUNT", "INFERRED")]
        # Stamped from the tenant's prospect edge, then linked.
        assert await _edges_from(provider, f"{org}-deal-legacy") == [(f"{org}-legacy", "FOR_ACCOUNT", "INFERRED")]
        assert await _edges_from(provider, f"{org}-deal-other") == []


async def test_an_account_is_an_entity_of_its_tenant_only(backend) -> None:
    provider, org = backend
    await provider.ensure_schema()
    await _seed(provider, org)
    await backfill(provider, GraphDataStore(logger, provider), org, apply=True, logger=logger, out=io.StringIO())
    # Even a link written by mistake does not reach another tenant's account.
    await provider.batch_create_entity_relations([{
        "_from": f"records/{org}-deal-other", "_to": f"organizations/{org}-other",
        "edgeType": "FOR_ACCOUNT", "createdAtTimestamp": 1,
    }])

    acme = {"id": f"{org}-acme", "type": "organization", "connectorIds": [f"{org}-conn"]}
    other = {"id": f"{org}-other", "type": "organization", "connectorIds": [f"{org}-conn"]}
    candidates = await provider.get_entity_candidate_records([acme, other], org)
    assert sorted(r["_key"] for r in candidates[("organization", f"{org}-acme")]) == [f"{org}-case", f"{org}-deal"]
    assert candidates[("organization", f"{org}-other")] == []

    permitted = await provider.get_permitted_entity_records(
        [acme, other], org, f"{org}-bob", app_level_connector_ids=[f"{org}-conn"],
    )
    assert sorted(r["_key"] for r in permitted[("organization", f"{org}-acme")]) == [f"{org}-case", f"{org}-deal"]
    assert permitted[("organization", f"{org}-other")] == []
    record_level = await provider.get_permitted_entity_records(
        [acme], org, f"{org}-bob", app_level_connector_ids=[],
    )
    assert record_level[("organization", f"{org}-acme")] == []

    membership = await provider.get_taxonomy_entity_membership(
        [{"id": f"{org}-acme", "type": "organization"}, {"id": f"{org}-other", "type": "organization"}], org,
    )
    assert membership[("organization", f"{org}-acme")]["connectorIds"] == [f"{org}-conn"]
    assert membership[("organization", f"{org}-other")] == {"connectorIds": [], "recordGroupIds": []}

    assert [o["id"] for o in await provider.get_record_organizations(f"{org}-case", org)] == [f"{org}-acme"]
    assert await provider.get_record_organizations(f"{org}-deal-other", org) == []

    rows = await provider.page_entity_index_source(ORGS, org, None, 10)
    assert [(r["_key"], r["name"]) for r in rows] == [(f"{org}-acme", "Acme"), (f"{org}-legacy", "Legacy Co")]
