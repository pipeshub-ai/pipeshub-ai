"""The ``person`` entity type of the entity listing queries and ``find_users``
on both providers. The client is mocked, so these pin the query shape and
binds; the Neo4j person query was checked against a real graph by hand."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.utils import person_query_ref
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

PERSON_REF = {
    "id": "u1", "type": "person", "connectorIds": ["c1"], "userIds": ["u1", "u2"],
    "outTypes": ["REPORTED_BY", "CREATED_BY", "DROP TABLE"], "inTypes": ["MENTIONED_IN", "ASSIGNED_TO"],
}
KWARGS = {"app_level_connector_ids": ["kb"], "limit_per_entity": 2, "offset": 0, "window": 100}
WITHIN = [{"apps": [], "groups": ["g1"], "records": []}]


def _neo4j(rows: list | None = None) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(return_value=rows or [])
    return p


def _arango(rows: list | None = None) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.execute_query = AsyncMock(return_value=rows or [])
    return p


class TestPersonQueryRef:
    def test_edges_are_kept_to_the_allow_list_of_their_direction(self) -> None:
        assert person_query_ref(PERSON_REF) == {
            "userIds": ["u1", "u2"],
            "outTypes": ["REPORTED_BY", "CREATED_BY"],
            "inTypes": ["MENTIONED_IN"],
        }

    def test_user_ids_default_to_the_ref_id(self) -> None:
        assert person_query_ref({"id": "u9"})["userIds"] == ["u9"]


class TestNeo4jPersonQuery:
    async def test_both_directions_with_the_person_at_the_user_end(self) -> None:
        p = _neo4j()
        await p.get_permitted_entity_records([PERSON_REF], "org1", "ukey", within=WITHIN, **KWARGS)
        query = p.client.execute_query.call_args.args[0]
        assert "MATCH (rec:Record)-[r:ENTITYRELATIONS]->(p:User)" in query
        assert "r.edgeType IN ref.outTypes" in query
        assert "MATCH (p:User)-[r:ENTITYRELATIONS]->(rec:Record)" in query
        assert "r.edgeType IN ref.inTypes" in query
        assert "p.id IN ref.userIds" in query

    async def test_candidates_are_filtered_narrowed_and_deduped_before_the_cap(self) -> None:
        p = _neo4j()
        await p.get_permitted_entity_records([PERSON_REF], "org1", "ukey", within=WITHIN, **KWARGS)
        query = p.client.execute_query.call_args.args[0]
        assert "rec.orgId = $org_id" in query
        assert "rec.connectorId IN ref.connectorIds" in query
        assert query.index("$within IS NULL") < query.index("WITH DISTINCT rec, u")
        assert query.index("WITH DISTINCT rec, u") < query.index("LIMIT $scan_cap") < query.index("ORDER BY")
        assert "UNWIND range(0, size(win) - 1) AS pos" in query

    async def test_binds_carry_the_allow_listed_edges_and_the_limit(self) -> None:
        p = _neo4j()
        await p.get_permitted_entity_records([PERSON_REF], "org1", "ukey", within=WITHIN, **KWARGS)
        params = p.client.execute_query.call_args.kwargs["parameters"]
        (ref,) = params["refs"]
        assert ref["outTypes"] == ["REPORTED_BY", "CREATED_BY"]
        assert ref["inTypes"] == ["MENTIONED_IN"]
        assert ref["userIds"] == ["u1", "u2"]
        assert params["within"] == WITHIN

    async def test_candidate_listing_supports_person_too(self) -> None:
        p = _neo4j()
        await p.get_entity_candidate_records([PERSON_REF], "org1")
        query = p.client.execute_query.call_args.args[0]
        assert "r.edgeType IN ref.outTypes" in query
        assert "WITH DISTINCT rec" in query

    async def test_taxonomy_refs_keep_their_relationship_match(self) -> None:
        p = _neo4j()
        await p.get_permitted_entity_records(
            [{"id": "t1", "type": "topic", "connectorIds": ["c1"]}], "org1", "ukey", **KWARGS,
        )
        query = p.client.execute_query.call_args.args[0]
        assert "MATCH (rec:Record)-[:BELONGS_TO_TOPIC]->(e)" in query
        assert "ENTITYRELATIONS" not in query


class TestArangoPersonQuery:
    async def test_both_directions_with_the_person_at_the_users_end(self) -> None:
        p = _arango()
        await p.get_permitted_entity_records([PERSON_REF], "org1", "ukey", within=WITHIN, **KWARGS)
        query = p.execute_query.await_args.args[0]
        assert 'CONCAT("users/", uid)' in query
        assert "edge._to IN people AND edge.edgeType IN ref.outTypes" in query
        assert "edge._from IN people AND edge.edgeType IN ref.inTypes" in query
        assert "FOR edge IN entityRelations" in query

    async def test_ids_are_distinct_then_filtered_and_narrowed_before_the_cap(self) -> None:
        p = _arango()
        await p.get_permitted_entity_records([PERSON_REF], "org1", "ukey", within=WITHIN, **KWARGS)
        query = p.execute_query.await_args.args[0]
        assert "UNION_DISTINCT(" in query
        assert query.index("UNION_DISTINCT(") < query.index("@within == null") < query.index("LIMIT @scan_cap")
        assert "rec.orgId == @org_id" in query
        assert query.index("LIMIT @scan_cap") < query.index("SORT") < query.index("LIMIT @offset, @window")

    async def test_refs_sent_carry_the_allow_listed_edges(self) -> None:
        p = _arango()
        await p.get_permitted_entity_records([PERSON_REF], "org1", "ukey", within=WITHIN, **KWARGS)
        binds = p.execute_query.await_args.kwargs["bind_vars"]
        (ref,) = binds["refs"]
        assert (ref["outTypes"], ref["inTypes"], ref["userIds"]) == (
            ["REPORTED_BY", "CREATED_BY"], ["MENTIONED_IN"], ["u1", "u2"],
        )
        assert binds["within"] == WITHIN

    async def test_candidate_listing_sends_the_same_refs(self) -> None:
        p = _arango()
        await p.get_entity_candidate_records([PERSON_REF], "org1")
        assert p.execute_query.await_args.kwargs["bind_vars"]["refs"][0]["inTypes"] == ["MENTIONED_IN"]


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> tuple[object, AsyncMock]:
    if request.param == "neo4j":
        p = _neo4j([{"id": "u1", "fullName": "Ann Lee", "email": "ann@x.io"}])
        return p, p.client.execute_query
    p = _arango([{"id": "u1", "fullName": "Ann Lee", "email": "ann@x.io"}])
    return p, p.execute_query


class TestFindUsers:
    async def test_query_is_normalized_and_bound(self, provider) -> None:
        p, call = provider
        users = await p.find_users("org1", "  Ann   LEE ", limit=5)
        assert users == [{"id": "u1", "fullName": "Ann Lee", "email": "ann@x.io"}]
        binds = call.await_args.kwargs.get("parameters") or call.await_args.kwargs["bind_vars"]
        assert binds == {"org_id": "org1", "q": "ann lee", "limit": 5}

    async def test_org_scoped_any_activity_and_same_email_nodes_together(self, provider) -> None:
        p, call = provider
        await p.find_users("org1", "ann")
        query = call.await_args.args[0]
        assert "isActive" not in query
        assert query.count("orgId") >= 2
        assert "people" in query
        assert ">= 3" in query

    async def test_an_email_is_matched_exactly_without_the_name_scan(self, provider) -> None:
        p, call = provider
        await p.find_users("org1", " Ann@X.io ")
        query = call.await_args.args[0]
        binds = call.await_args.kwargs.get("parameters") or call.await_args.kwargs["bind_vars"]
        assert binds["q"] == "ann@x.io"
        assert "orgId" in query and "email" in query
        assert "split" not in query.lower() and "fullName ==" not in query

    async def test_the_query_is_lowercased_like_the_stored_side(self, provider) -> None:
        p, call = provider
        await p.find_users("org1", "Weiß")
        binds = call.await_args.kwargs.get("parameters") or call.await_args.kwargs["bind_vars"]
        assert binds["q"] == "weiß"

    async def test_name_matches_are_bounded_before_the_email_expansion(self, provider) -> None:
        p, call = provider
        await p.find_users("org1", "ann")
        query = call.await_args.args[0]
        assert "[0..$limit] AS people" in query or "LIMIT @limit" in query

    async def test_matches_are_ordered_before_the_cut_and_people_are_never_cut(self, provider) -> None:
        p, call = provider
        await p.find_users("org1", "ann", limit=3)
        query = call.await_args.args[0]
        cut = query.index("[0..$limit]") if "[0..$limit]" in query else query.index("LIMIT @limit")
        order = query.index("ORDER BY name, u.id") if "ORDER BY name" in query else query.index("SORT name, u._key")
        assert order < cut
        # Only the name matches are bounded; the same-email expansion is not.
        assert query.count("$limit") + query.count("@limit") == 1

    async def test_an_email_returns_all_of_its_nodes(self, provider) -> None:
        p, call = provider
        await p.find_users("org1", "ann@x.io", limit=1)
        query = call.await_args.args[0]
        binds = call.await_args.kwargs.get("parameters") or call.await_args.kwargs["bind_vars"]
        assert "limit" not in query.lower() and "limit" not in binds

    async def test_blank_query_makes_no_call(self, provider) -> None:
        p, call = provider
        assert await p.find_users("org1", "   ") == []
        call.assert_not_awaited()
