"""A record found by its own name is returned whether or not its content is indexed.

Only the record branch of the entity queries drops the "indexed only" rule; the
taxonomy, record-group and person branches list the records of an entity and
keep it, because they offer records whose content can be read. Org, trash,
connector, record-type, scope and permission checks stay on every branch.

The client is mocked, so these pin the query text, the binds and the row keys
on both providers. ``tests/integration/graph_db/test_permitted_entity_records_real_backends.py``
runs the same fixture against real servers.
"""
from __future__ import annotations

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.record_visibility import (
    aql_searchable_record,
    cypher_searchable_record,
)
from app.services.graph_db.common.utils import PERSON_ENTITY_TYPE
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

RECORD_REF = {"id": "r1", "type": "record", "connectorIds": ["c1"]}
TOPIC_REF = {"id": "t1", "type": "topic", "connectorIds": ["c1"]}
KWARGS = {"app_level_connector_ids": ["c1"], "limit_per_entity": 2, "window": 20}

NON_RECORD_ARANGO = [*ArangoHTTPProvider._ENTITY_CANDIDATE_EDGE_TARGETS, PERSON_ENTITY_TYPE]
NON_RECORD_NEO4J = [*Neo4jProvider._ENTITY_CANDIDATE_TARGETS, PERSON_ENTITY_TYPE]

ARANGO_GATE = aql_searchable_record("rec", "completed")
NEO4J_GATE = cypher_searchable_record("rec", "completed")


def _arango() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.execute_query = AsyncMock(return_value=[])
    return p


def _neo4j() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=[])
    return p


class TestArangoQueryText:
    def test_the_candidate_record_branch_has_no_indexed_gate(self) -> None:
        query = _arango()._entity_candidate_records_aql("record", filter_record_types=True)
        assert "@completed" not in query
        assert "hasIndexedCopy == true" not in query
        assert "rec.orgId == @org_id" in query
        assert "rec.isDeleted != true" in query
        assert "rec.connectorId IN ref.connectorIds" in query
        assert "rec.recordType IN @record_types" in query

    def test_the_permitted_record_branch_has_no_indexed_gate(self) -> None:
        query = _arango()._permitted_entity_records_aql("record", filter_record_types=True)
        assert "@completed" not in query
        assert "hasIndexedCopy == true" not in query
        assert "one.orgId == @org_id" in query
        assert "one.isDeleted != true" in query
        assert "one.connectorId IN ref.connectorIds" in query
        assert "one.recordType IN @record_types" in query
        # The turn's limit and the permission walk still apply.
        assert "@within" in query
        assert "app_granted OR checked[0] == true" in query

    @pytest.mark.parametrize("entity_type", NON_RECORD_ARANGO)
    def test_the_other_branches_keep_the_gate(self, entity_type: str) -> None:
        provider = _arango()
        assert ARANGO_GATE in provider._entity_candidate_records_aql(entity_type, filter_record_types=False)
        assert ARANGO_GATE in provider._permitted_entity_records_aql(entity_type, filter_record_types=False)

    @pytest.mark.asyncio
    async def test_a_record_query_binds_no_completed_value(self) -> None:
        """Arango rejects a bind var the query does not use; the other types still need it."""
        provider = _arango()
        await provider.get_entity_candidate_records([RECORD_REF, TOPIC_REF], "org1")
        record_binds, topic_binds = (c.kwargs["bind_vars"] for c in provider.execute_query.await_args_list)
        assert "completed" not in record_binds
        assert topic_binds["completed"] == "COMPLETED"

        provider.execute_query.reset_mock()
        await provider.get_permitted_entity_records([RECORD_REF, TOPIC_REF], "org1", "ukey", **KWARGS)
        record_binds, topic_binds = (c.kwargs["bind_vars"] for c in provider.execute_query.await_args_list)
        assert "completed" not in record_binds
        assert topic_binds["completed"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_every_bind_var_of_a_record_query_is_used(self) -> None:
        provider = _arango()
        await provider.get_permitted_entity_records(
            [RECORD_REF], "org1", "ukey", record_types=["FILE"], within=[{"apps": ["c1"], "groups": [], "records": []}],
            **KWARGS,
        )
        call = provider.execute_query.await_args
        used = set(re.findall(r"@(\w+)", call.args[0]))
        assert set(call.kwargs["bind_vars"]) == used


class TestNeo4jQueryText:
    def test_the_candidate_record_branch_has_no_indexed_gate(self) -> None:
        query = _neo4j()._entity_candidate_records_cypher("record")
        assert NEO4J_GATE not in query
        assert "hasIndexedCopy = true" not in query
        assert "rec.orgId = $org_id" in query
        assert "coalesce(rec.isDeleted, false) = false" in query
        assert "rec.connectorId IN ref.connectorIds" in query
        assert "rec.recordType IN $record_types" in query

    def test_the_permitted_record_branch_has_no_indexed_gate(self) -> None:
        query = _neo4j()._permitted_entity_records_cypher("record")
        assert NEO4J_GATE not in query
        assert "hasIndexedCopy = true" not in query
        assert "rec.orgId = $org_id" in query
        assert "coalesce(rec.isDeleted, false) = false" in query
        assert "rec.connectorId IN ref.connectorIds" in query
        assert "($within IS NULL OR all(s IN $within" in query
        assert "permission_role IS NOT NULL" in query

    @pytest.mark.parametrize("entity_type", NON_RECORD_NEO4J)
    def test_the_other_branches_keep_the_gate(self, entity_type: str) -> None:
        """The record branch has its own filter; the shared one was not loosened."""
        provider = _neo4j()
        assert NEO4J_GATE in provider._entity_candidate_records_cypher(entity_type)
        assert NEO4J_GATE in provider._permitted_entity_records_cypher(entity_type)
        assert NEO4J_GATE in Neo4jProvider._ENTITY_CANDIDATE_RECORD_FILTER


class TestRowShape:
    def test_both_providers_project_the_same_keys_with_the_indexing_state(self) -> None:
        arango_keys = set(ArangoHTTPProvider._ENTITY_CANDIDATE_RECORD_FIELDS)
        neo4j_keys = {"_key", *re.findall(r"(?<=[ {])\.(\w+)", Neo4jProvider._ENTITY_CANDIDATE_RECORD_PROJECTION)}
        assert {"indexingStatus", "hasIndexedCopy"} <= arango_keys
        assert arango_keys == neo4j_keys

    def test_the_arango_projection_names_absent_fields_as_null(self) -> None:
        projection = ArangoHTTPProvider._entity_candidate_record_projection("rec")
        assert '"indexingStatus": rec.indexingStatus' in projection
        assert '"hasIndexedCopy": rec.hasIndexedCopy' in projection
