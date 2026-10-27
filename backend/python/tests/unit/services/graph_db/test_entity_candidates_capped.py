"""At the provider: both graph backends report, per entity, whether the
candidate scan stopped at ``ENTITY_CANDIDATE_SCAN_CAP``, and sort and page
only after the capped scan is collected.

The client is mocked, so these pin the query shape and the result handling;
``tests/integration/graph_db/test_entity_candidates_real_backends.py`` runs
the queries against real servers.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.utils import EntityCandidateRows
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

TOPIC_REF = {"id": "t1", "type": "topic", "connectorIds": ["c1"]}
RECORD_REF = {"id": "r1", "type": "record", "connectorIds": ["c1"]}


def _neo4j(rows: list[dict]) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=rows)
    return p


def _arango(rows: list[dict]) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.execute_query = AsyncMock(return_value=rows)
    return p


class TestNeo4jCapped:
    async def test_scan_is_collected_then_sorted_and_capped_is_returned(self) -> None:
        p = _neo4j([])
        await p.get_entity_candidate_records([TOPIC_REF], "org1")
        query = p.client.execute_query.call_args.args[0]
        assert query.index("LIMIT $scan_cap") < query.index("collect(rec) AS scanned") < query.index("ORDER BY")
        assert "size(scanned) >= $scan_cap AS capped" in query
        assert "RETURN ref.id AS id, rows, capped" in query

    async def test_capped_flag_is_carried_per_entity(self) -> None:
        row = {"_key": "rec1"}
        p = _neo4j([{"id": "t1", "rows": [row], "capped": True}, {"id": "t2", "rows": [], "capped": False}])
        out = await p.get_entity_candidate_records(
            [TOPIC_REF, {**TOPIC_REF, "id": "t2"}], "org1",
        )
        assert out[("topic", "t1")] == [row]
        assert isinstance(out[("topic", "t1")], EntityCandidateRows)
        assert out[("topic", "t1")].capped is True
        assert out[("topic", "t2")].capped is False

    async def test_unqueried_and_record_refs_are_never_capped(self) -> None:
        p = _neo4j([{"id": "r1", "rows": [{"_key": "r1"}]}])
        out = await p.get_entity_candidate_records([RECORD_REF], "org1")
        assert out[("record", "r1")].capped is False


class TestArangoCapped:
    async def test_scan_is_collected_then_sorted_and_capped_is_returned(self) -> None:
        p = _arango([])
        await p.get_entity_candidate_records([TOPIC_REF], "org1")
        query = p.execute_query.await_args.args[0]
        assert query.index("LET scanned") < query.index("LIMIT @scan_cap") < query.index("SORT")
        assert "capped: LENGTH(scanned) >= @scan_cap" in query

    async def test_record_group_scope_check_still_guards_the_scan(self) -> None:
        p = _arango([])
        await p.get_entity_candidate_records(
            [{"id": "rg1", "type": "record_group", "connectorIds": ["c1"]}], "org1",
        )
        query = p.execute_query.await_args.args[0]
        assert "(rg != null AND rg.orgId == @org_id) ?" in query
        assert query.index("(rg != null AND rg.orgId == @org_id) ?") < query.index("LIMIT @scan_cap")

    async def test_capped_flag_is_carried_per_entity(self) -> None:
        row = {"_key": "rec1"}
        p = _arango([{"id": "t1", "rows": [row], "capped": True}])
        out = await p.get_entity_candidate_records([TOPIC_REF], "org1")
        assert out[("topic", "t1")] == [row]
        assert out[("topic", "t1")].capped is True

    async def test_ref_without_connectors_is_empty_and_uncapped(self) -> None:
        p = _arango([])
        out = await p.get_entity_candidate_records([{**TOPIC_REF, "connectorIds": []}], "org1")
        assert out[("topic", "t1")] == []
        assert out[("topic", "t1")].capped is False
