"""Provider pieces of extracted organisations (KG-13 slice 3b), on both
backends, with mocked clients. tests/integration/graph_db/
test_extracted_organizations_real_backends.py runs them against servers."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _neo4j(rows: list | None = None) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=rows or [])
    return p


def _arango(rows: list | None = None) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(return_value=rows or [])
    return p


class TestFindOrganizations:
    async def test_arango_looks_within_the_tenant_by_key(self) -> None:
        p = _arango([{"id": "a", "name": "Acme", "normalizedName": "acme"}])
        assert await p.find_organizations("t", ["acme", "acme", ""]) == [{"id": "a", "name": "Acme", "normalizedName": "acme"}]
        query = p.http_client.execute_aql.await_args.args[0]
        assert "o.parentOrgId == @org_id AND o.normalizedName IN @keys AND o.isExternal == true" in query
        assert p.http_client.execute_aql.await_args.kwargs["bind_vars"]["keys"] == ["acme"]

    async def test_neo4j_looks_within_the_tenant_by_key(self) -> None:
        p = _neo4j()
        await p.find_organizations("t", ["acme"])
        query = p.client.execute_query.await_args.args[0]
        assert "MATCH (o:Organization)" in query
        assert "o.parentOrgId = $org_id AND o.normalizedName IN $keys AND o.isExternal = true" in query

    @pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
    async def test_nothing_to_find_costs_no_query(self, make) -> None:
        p = make()
        assert await p.find_organizations("t", []) == [] and await p.find_organizations("", ["x"]) == []


class TestCreateOrganization:
    async def test_arango_never_overwrites_an_existing_node(self) -> None:
        p = _arango()
        p.http_client.batch_insert_documents = AsyncMock(return_value={"errors": 0})
        await p.create_organization_if_absent("t", {"id": "k", "name": "Initech", "normalizedName": "initech"})
        (collection, (doc,)), kwargs = p.http_client.batch_insert_documents.await_args
        assert collection == "organizations" and kwargs["overwrite_mode"] == "ignore"
        assert {f: doc[f] for f in ("_key", "isExternal", "parentOrgId", "accountType", "isActive")} == {
            "_key": "k", "isExternal": True, "parentOrgId": "t", "accountType": "enterprise", "isActive": True,
        }

    async def test_neo4j_sets_fields_on_create_only(self) -> None:
        p = _neo4j()
        await p.create_organization_if_absent("t", {"id": "k", "name": "Initech"})
        query = p.client.execute_query.await_args.args[0]
        assert "MERGE (o:Organization {id: $id})" in query and "ON CREATE SET o += $props" in query

    @pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
    async def test_a_node_needs_a_tenant_id_and_name(self, make) -> None:
        with pytest.raises(ValueError):
            await make().create_organization_if_absent("", {"id": "k", "name": "n"})
        with pytest.raises(ValueError):
            await make().create_organization_if_absent("t", {"id": "k", "name": ""})


class TestDeleteByOrigin:
    async def test_arango_counts_an_edge_without_origin_as_inferred(self) -> None:
        p = _arango([1, 1])
        assert await p.delete_record_entity_relations("r", "organizations", "INFERRED") == 2
        query = p.http_client.execute_aql.await_args.args[0]
        binds = p.http_client.execute_aql.await_args.kwargs["bind_vars"]
        assert "FILTER NOT_NULL(e.origin, @inferred) == @origin" in query
        assert (binds["from"], binds["to"], binds["inferred"]) == ("records/r", "organizations", "INFERRED")

    async def test_neo4j_counts_an_edge_without_origin_as_inferred(self) -> None:
        p = _neo4j([{"deleted": 1}])
        assert await p.delete_record_entity_relations("r", "organizations", "EXTRACTED") == 1
        query = p.client.execute_query.await_args.args[0]
        assert "MATCH (:Record {id: $record_id})-[e:ENTITYRELATIONS]->(:Organization)" in query
        assert "coalesce(e.origin, $inferred) = $origin" in query

    @pytest.mark.parametrize("make", [_neo4j, _arango], ids=["neo4j", "arango"])
    async def test_an_origin_is_required(self, make) -> None:
        with pytest.raises(ValueError):
            await make().delete_record_entity_relations("r", "organizations", "")


class TestReach:
    async def test_arango_counts_live_records_and_connector_edges(self) -> None:
        p = _arango([{"key": "k", "records": 2, "inferred": False}])
        assert await p.get_organization_record_reach("t", ["k"]) == {"k": {"records": 2, "inferred": False}}
        query = p.http_client.execute_aql.await_args.args[0]
        assert "r.isDeleted != true" in query
        assert "INBOUND o dealOf, prospect," in query and "customer" in query

    async def test_neo4j_counts_live_records_and_connector_edges(self) -> None:
        p = _neo4j([{"key": "k", "records": 0, "inferred": True}])
        assert await p.get_organization_record_reach("t", ["k"]) == {"k": {"records": 0, "inferred": True}}
        query = p.client.execute_query.await_args.args[0]
        assert "r.isDeleted IS NULL OR r.isDeleted = false" in query
        assert "EXISTS {" in query


class TestIndexes:
    async def test_arango_indexes_the_tenant_and_key_sparsely(self) -> None:
        p = _arango()
        p.http_client.ensure_persistent_index = AsyncMock()
        await p._ensure_indexes()
        calls = [c for c in p.http_client.ensure_persistent_index.await_args_list
                 if c.args[:2] == ("organizations", ["parentOrgId", "normalizedName"])]
        assert len(calls) == 1 and calls[0].kwargs.get("sparse") is True

    def test_neo4j_indexes_the_tenant_and_key(self) -> None:
        statements = _neo4j()._generate_performance_indexes()
        assert any("FOR (n:Organization) ON (n.parentOrgId, n.normalizedName)" in s for s in statements)
