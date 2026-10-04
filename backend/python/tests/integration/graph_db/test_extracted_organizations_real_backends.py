"""Against real servers (KG-13 slice 3b): organisations extracted from
document content. On Neo4j 5.26 and ArangoDB 3.12.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_extracted_organizations_real_backends.py -m integration
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.config.constants.arangodb import CollectionNames, EntityOrigin, EntityRelations
from app.models.entities import DealRecord, RecordGroupType, RecordType
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.models import (
    ORGANIZATION,
    EntityResolution,
    ResolutionMode,
    ResolvedEntity,
)
from app.modules.transformers.graphdb import GraphDBTransformer
from tests.integration.graph_db.test_record_organizations_real_backends import (
    ORGS,
    _account,
    _base,
    _edges_from,
    backend,  # noqa: F401 - the fixture
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
RECORDS = CollectionNames.RECORDS.value


def _edge(org: str, record: str, target: str, edge_type: str, origin: str | None) -> dict:
    edge = {"_from": f"{RECORDS}/{org}-{record}", "_to": f"{ORGS}/{target}", "edgeType": edge_type,
            "createdAtTimestamp": 1}
    if origin:
        edge["origin"] = origin
    return edge


async def _records(provider: Any, org: str, *keys: str) -> None:  # noqa: ANN401
    await provider.batch_upsert_records([
        DealRecord(
            record_name=key, record_type=RecordType.DEAL, record_group_type=RecordGroupType.DEAL,
            external_record_group_id="001A", **_base(org, key, "rg"),
        )
        for key in keys
    ])


async def test_an_extracted_organisation_is_created_once_for_its_tenant_and_found_by_key(backend) -> None:  # noqa: F811
    provider, org = backend
    await provider.ensure_schema()
    await provider.batch_upsert_nodes([{"id": org, "accountType": "enterprise", "isActive": True}], ORGS)
    node = {"id": f"{org}-x-globex", "name": "Globex Corporation", "normalizedName": "globex"}
    await provider.create_organization_if_absent(org, node)
    await provider.create_organization_if_absent(org, {**node, "name": "Globex"})  # a no-op
    # A Salesforce account carrying its key, and another tenant's namesake.
    await provider.batch_upsert_nodes([
        {**_account(org, "acme", "Acme Corp", parent=org), "normalizedName": "acme"},
        {**_account(org, "theirs", "Globex", parent=f"{org}-other"), "normalizedName": "globex"},
    ], ORGS)

    rows = await provider.find_organizations(org, ["globex", "acme", "initech"])
    assert sorted((r["id"], r["name"], r["normalizedName"]) for r in rows) == [
        (f"{org}-acme", "Acme Corp", "acme"), (f"{org}-x-globex", "Globex Corporation", "globex"),
    ]
    (created,) = await provider.get_nodes_by_field_in(
        ORGS, "id", [f"{org}-x-globex"], return_fields=["id", "isExternal", "parentOrgId", "accountType", "isActive"],
    )
    assert (created["isExternal"], created["parentOrgId"], created["isActive"]) == (True, org, True)
    assert await provider.find_organizations(org, []) == []


async def test_each_writer_clears_only_its_own_origin(backend) -> None:  # noqa: F811
    provider, org = backend
    await provider.ensure_schema()
    await provider.batch_upsert_nodes([
        _account(org, "acme", "Acme", parent=org), _account(org, "globex", "Globex", parent=org),
    ], ORGS)
    await _records(provider, org, "deal")
    await provider.batch_create_entity_relations([
        _edge(org, "deal", f"{org}-acme", EntityRelations.FOR_ACCOUNT.value, EntityOrigin.INFERRED.value),
        _edge(org, "deal", f"{org}-globex", EntityRelations.MENTIONS.value, EntityOrigin.EXTRACTED.value),
    ])
    assert await provider.delete_record_entity_relations(f"{org}-deal", ORGS, EntityOrigin.EXTRACTED.value) == 1
    assert await _edges_from(provider, f"{org}-deal") == [(f"{org}-acme", "FOR_ACCOUNT", "INFERRED")]

    # An edge from before origins existed came from a connector.
    await provider.batch_create_entity_relations([
        _edge(org, "deal", f"{org}-acme", EntityRelations.FOR_ACCOUNT.value, None),
        _edge(org, "deal", f"{org}-globex", EntityRelations.MENTIONS.value, EntityOrigin.EXTRACTED.value),
    ])
    assert await provider.delete_record_entity_relations(f"{org}-deal", ORGS, EntityOrigin.INFERRED.value) == 1
    assert await _edges_from(provider, f"{org}-deal") == [(f"{org}-globex", "MENTIONS", "EXTRACTED")]


async def test_reach_counts_live_records_naming_it_and_whether_a_connector_knows_it(backend) -> None:  # noqa: F811
    provider, org = backend
    await provider.ensure_schema()
    await provider.batch_upsert_nodes([
        _account(org, "named", "Named", parent=org),
        _account(org, "account", "Account", parent=org),
        _account(org, "linked", "Linked", parent=org),
        _account(org, "theirs", "Theirs", parent=f"{org}-x"),
    ], ORGS)
    await provider.batch_upsert_nodes([{
        "id": f"{org}-rg", "groupName": "rg", "groupType": RecordGroupType.SALESFORCE_ORG.value,
        "connectorName": "SALESFORCE", "connectorId": f"{org}-conn", "orgId": org, "externalGroupId": "001A",
        "createdAtTimestamp": 1,
    }], CollectionNames.RECORD_GROUPS.value)
    await provider.batch_create_edges([{
        "from_id": f"{org}-rg", "from_collection": CollectionNames.RECORD_GROUPS.value,
        "to_id": f"{org}-account", "to_collection": ORGS, "createdAtTimestamp": 1,
    }], CollectionNames.DEAL_OF.value)
    await _records(provider, org, "r1", "r2", "r3", "gone")
    await provider.batch_update_nodes([{"id": f"{org}-gone", "isDeleted": True}], RECORDS)
    mention = EntityRelations.MENTIONS.value
    await provider.batch_create_entity_relations([
        _edge(org, "r1", f"{org}-named", mention, EntityOrigin.EXTRACTED.value),
        _edge(org, "gone", f"{org}-named", mention, EntityOrigin.EXTRACTED.value),
        _edge(org, "r1", f"{org}-account", mention, EntityOrigin.EXTRACTED.value),
        _edge(org, "r2", f"{org}-linked", EntityRelations.FOR_ACCOUNT.value, EntityOrigin.INFERRED.value),
        _edge(org, "r3", f"{org}-theirs", mention, EntityOrigin.EXTRACTED.value),
    ])

    reach = await provider.get_organization_record_reach(
        org, [f"{org}-named", f"{org}-account", f"{org}-linked", f"{org}-theirs", f"{org}-none"],
    )
    assert reach[f"{org}-named"] == {"records": 1, "inferred": False}  # the trashed record does not count
    assert reach[f"{org}-account"] == {"records": 1, "inferred": True}  # a CRM account's group points at it
    assert reach[f"{org}-linked"] == {"records": 0, "inferred": True}
    assert f"{org}-theirs" not in reach and f"{org}-none" not in reach

    await _records(provider, org, "r4")
    await provider.batch_create_entity_relations([_edge(org, "r4", f"{org}-named", mention, EntityOrigin.EXTRACTED.value)])
    assert (await provider.get_organization_record_reach(org, [f"{org}-named"]))[f"{org}-named"]["records"] == 2
    await _records(provider, org, "r5")
    await provider.batch_create_entity_relations([_edge(org, "r5", f"{org}-named", mention, EntityOrigin.EXTRACTED.value)])
    capped = await provider.get_organization_record_reach(org, [f"{org}-named"], record_cap=2)
    assert capped[f"{org}-named"] == {"records": 2, "inferred": False}


async def test_the_graph_write_links_a_record_to_what_it_names_and_reports_what_is_searchable(backend) -> None:  # noqa: F811
    """GraphDBTransformer against the server: the new organisation and its
    MENTIONS edges pass the strict schemas, and a re-extraction replaces only
    the record's EXTRACTED links."""
    provider, org = backend
    await provider.ensure_schema()
    await provider.batch_upsert_nodes([_account(org, "acme", "Acme", parent=org)], ORGS)
    await _records(provider, org, "r1", "r2")
    await provider.batch_create_entity_relations([
        _edge(org, "r1", f"{org}-acme", EntityRelations.FOR_ACCOUNT.value, EntityOrigin.INFERRED.value),
    ])
    transformer = GraphDBTransformer(graph_provider=provider, logger=logging.getLogger("t"))
    key = taxonomy_node_key(org, ORGS, "initech")

    def _resolution(*names: str) -> EntityResolution:
        resolution = EntityResolution(org_id=org, mode=ResolutionMode.APPLY)
        for name in names:
            resolution.add(ResolvedEntity(
                kind=ORGANIZATION, key=taxonomy_node_key(org, ORGS, name.casefold()), name=name,
                normalized=name.casefold(), is_new=True, decision="new", extracted_names=[name],
            ))
        return resolution

    def _record(rid: str) -> SimpleNamespace:
        return SimpleNamespace(id=f"{org}-{rid}", connector_id=f"{org}-conn", record_group_id=None)

    try:
        assert await transformer._link_organizations(_record("r1"), _resolution("Initech")) == []
        assert await _edges_from(provider, f"{org}-r1") == sorted([
            (f"{org}-acme", "FOR_ACCOUNT", "INFERRED"), (key, "MENTIONS", "EXTRACTED"),
        ])
        (searchable,) = await transformer._link_organizations(_record("r2"), _resolution("Initech"))
        assert (searchable.entity_id, searchable.name) == (key, "Initech")
        assert searchable.connector_ids == [f"{org}-conn"]  # read from the graph's membership

        await transformer._link_organizations(_record("r1"), _resolution("Hooli"))
        assert await _edges_from(provider, f"{org}-r1") == sorted([
            (f"{org}-acme", "FOR_ACCOUNT", "INFERRED"), (taxonomy_node_key(org, ORGS, "hooli"), "MENTIONS", "EXTRACTED"),
        ])
    finally:
        await provider.delete_nodes_and_edges([key, taxonomy_node_key(org, ORGS, "hooli")], ORGS)
