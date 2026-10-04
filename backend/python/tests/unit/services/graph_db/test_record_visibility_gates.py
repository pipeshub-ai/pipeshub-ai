"""The live-record gates on both graph providers.

A record in the trash (``isDeleted`` true) keeps its node, edges and permission
edges so it can be restored. These are the reads that must not see it: the
search permission map, the access check, search hydration, dedup, and the
connector's parent, status and failed-record listings. Each test runs the real
provider method against a stubbed client and looks at the query it sent, or at
what it returned when the filtering happens in Python.

Real-database behaviour is in tests/integration/test_record_visibility_e2e.py.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    aql_live_record,
    aql_record_visibility,
    cypher_live_record,
    cypher_record_visibility,
    is_live_record,
    matches_visibility,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ARANGO_LIVE = "{v}.isDeleted != true"
NEO4J_LIVE = "({v}.isDeleted IS NULL OR {v}.isDeleted = false)"


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), AsyncMock())
    provider.http_client = AsyncMock()
    provider.http_client.execute_aql = AsyncMock(return_value=[])
    return provider


def _neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.client.execute_query = AsyncMock(return_value=[])
    return provider


def _query_of(call: object) -> str:
    return call.args[0] if call.args else call.kwargs["query"]


def _arango_queries(provider: ArangoHTTPProvider) -> list[str]:
    return [_query_of(c) for c in provider.http_client.execute_aql.await_args_list]


def _neo4j_queries(provider: Neo4jProvider) -> list[str]:
    return [_query_of(c) for c in provider.client.execute_query.await_args_list]


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------


class TestPredicate:
    def test_a_record_without_the_field_is_live(self) -> None:
        """No migration: records written before soft delete have no isDeleted."""
        assert is_live_record({"_key": "r1"})
        assert is_live_record({"_key": "r1", "isDeleted": None})
        assert is_live_record({"_key": "r1", "isDeleted": False})
        assert not is_live_record({"_key": "r1", "isDeleted": True})

    def test_it_reads_a_record_model_too(self) -> None:
        assert is_live_record(MagicMock(is_deleted=False))
        assert not is_live_record(MagicMock(is_deleted=True))

    @pytest.mark.parametrize(
        ("visibility", "deleted", "expected"),
        [
            (RecordVisibility.LIVE, False, True),
            (RecordVisibility.LIVE, True, False),
            (RecordVisibility.DELETED, False, False),
            (RecordVisibility.DELETED, True, True),
            (RecordVisibility.ALL, False, True),
            (RecordVisibility.ALL, True, True),
        ],
    )
    def test_matches_visibility(self, visibility, deleted, expected) -> None:
        assert matches_visibility({"isDeleted": deleted}, visibility) is expected

    def test_query_forms(self) -> None:
        assert aql_live_record("r") == ARANGO_LIVE.format(v="r")
        assert cypher_live_record("r") == NEO4J_LIVE.format(v="r")
        assert aql_record_visibility("r", RecordVisibility.DELETED) == "r.isDeleted == true"
        assert cypher_record_visibility("r", RecordVisibility.DELETED) == "r.isDeleted = true"
        assert aql_record_visibility("r", RecordVisibility.ALL) == "true"
        assert cypher_record_visibility("r", RecordVisibility.ALL) == "true"

    def test_neo4j_form_is_null_safe(self) -> None:
        """`<> true` is null for a node without the property, and WHERE drops null."""
        assert "<> true" not in cypher_live_record("r")


# ---------------------------------------------------------------------------
# Methods whose caller picks the visibility
# ---------------------------------------------------------------------------

# (method, kwargs, the variable the query filters)
PARAM_CALLS: list[tuple[str, dict[str, Any], dict[str, str]]] = [
    (
        "get_record_by_external_id",
        {"connector_id": "c1", "external_id": "e1"},
        {"arango": "doc", "neo4j": "r"},
    ),
    (
        "get_records_by_status",
        {"org_id": "o1", "connector_id": "c1", "status_filters": ["FAILED"]},
        {"arango": "record", "neo4j": "r"},
    ),
    (
        "get_records_by_parent",
        {"connector_id": "c1", "parent_external_record_id": "p1"},
        {"arango": "record", "neo4j": "record"},
    ),
    (
        "get_records_by_record_ids",
        {"record_ids": ["r1"], "org_id": "o1"},
        {"arango": "record", "neo4j": "r"},
    ),
]


def _expected(backend: str, var: str, visibility: RecordVisibility) -> str:
    if backend == "arango":
        return aql_record_visibility(var, visibility)
    return cypher_record_visibility(var, visibility)


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
@pytest.mark.parametrize(("method", "kwargs", "var"), PARAM_CALLS, ids=[c[0] for c in PARAM_CALLS])
class TestVisibilityParameter:
    async def test_live_by_default(self, backend, method, kwargs, var) -> None:
        provider = _arango() if backend == "arango" else _neo4j()
        await getattr(provider, method)(**kwargs)
        queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
        assert any(_expected(backend, var[backend], RecordVisibility.LIVE) in q for q in queries), queries

    async def test_deleted_selects_only_the_trash(self, backend, method, kwargs, var) -> None:
        provider = _arango() if backend == "arango" else _neo4j()
        await getattr(provider, method)(**kwargs, visibility=RecordVisibility.DELETED)
        queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
        assert any(_expected(backend, var[backend], RecordVisibility.DELETED) in q for q in queries), queries

    async def test_all_filters_nothing(self, backend, method, kwargs, var) -> None:
        provider = _arango() if backend == "arango" else _neo4j()
        await getattr(provider, method)(**kwargs, visibility=RecordVisibility.ALL)
        queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
        assert queries
        assert not any("isDeleted" in q for q in queries), queries


# ---------------------------------------------------------------------------
# Gates that are always live-only
# ---------------------------------------------------------------------------


class TestPermissionMap:
    """The accessible-record map decides what search may return.

    Every permission path gets the filter, and it runs before the VRID is
    collapsed to one record id, so a VRID shared by a live and a trashed record
    resolves to the live one.
    """

    async def test_arango_connector_map_filters_every_path(self) -> None:
        provider = _arango()
        await provider._get_virtual_ids_for_connector("u1", "o1", "c1", raise_on_error=True)
        (query,) = _arango_queries(provider)
        paths = query.count("FILTER record.indexingStatus == @completedStatus")
        # Seven since #3691 dropped the "anyone" path, which granted no access.
        assert paths == 7
        assert query.count(f"FILTER {aql_live_record('record')}") == paths
        assert query.index(aql_live_record("record")) < query.index("COLLECT virtualRecordId")

    async def test_arango_kb_map_filters_every_path(self) -> None:
        provider = _arango()
        await provider._get_kb_virtual_ids("u1", "o1", raise_on_error=True)
        (query,) = _arango_queries(provider)
        paths = query.count("FILTER record.indexingStatus == @completedStatus")
        assert paths == 2
        assert query.count(f"FILTER {aql_live_record('record')}") == paths

    async def test_neo4j_connector_map_filters_every_path(self) -> None:
        provider = _neo4j()
        await provider._get_virtual_ids_for_connector("u1", "o1", "c1", raise_on_error=True)
        (query,) = _neo4j_queries(provider)
        paths = query.count("r.indexingStatus = $completedStatus")
        assert paths == 7
        assert query.count(f"AND {cypher_live_record('r')}") == paths

    async def test_neo4j_kb_map_filters_every_path(self) -> None:
        provider = _neo4j()
        await provider._get_kb_virtual_ids("u1", "o1", raise_on_error=True)
        (query,) = _neo4j_queries(provider)
        paths = query.count("r.indexingStatus = $completedStatus")
        assert paths == 2
        assert query.count(f"AND {cypher_live_record('r')}") == paths

    async def test_neo4j_cached_kb_map_filters_too(self) -> None:
        """The cached per-KB map is what an unfiltered search actually reads."""
        provider = _neo4j()
        await provider._get_kb_virtual_ids_for_kb("kb1")
        (query,) = _neo4j_queries(provider)
        assert cypher_live_record("r") in query


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
class TestAccessCheck:
    async def test_a_trashed_record_has_no_access(self, backend) -> None:
        """Returned before the access query: a trashed record keeps its edges."""
        provider = _arango() if backend == "arango" else _neo4j()
        if backend == "neo4j":
            provider.client.execute_query = AsyncMock(return_value=[{"u": {"id": "uk1", "userId": "u1"}}])
        else:
            provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1", "userId": "u1"})
        provider._get_user_app_ids = AsyncMock(return_value=["c1"])
        provider.get_document = AsyncMock(return_value={"_key": "r1", "id": "r1", "isDeleted": True})

        assert await provider.check_record_access_with_details("u1", "o1", "r1") is None

        # Neo4j reads the user through the client; the access query never runs.
        queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
        assert len(queries) == (0 if backend == "arango" else 1)


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
class TestDedup:
    async def test_duplicates_are_live_only(self, backend) -> None:
        """A copy that took COMPLETED from a trashed record would have no vectors."""
        provider = _arango() if backend == "arango" else _neo4j()
        await provider.find_duplicate_records("r1", "md5", "o1")
        queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
        live = aql_live_record("record") if backend == "arango" else cypher_live_record("r")
        assert live in queries[0]

    async def test_the_next_queued_duplicate_is_live(self, backend) -> None:
        provider = _arango() if backend == "arango" else _neo4j()
        ref = {"_key": "r1", "id": "r1", "md5Checksum": "md5", "orgId": "o1", "isDeleted": True}
        if backend == "arango":
            provider.http_client.execute_aql = AsyncMock(side_effect=[[ref], []])
        else:
            provider.client.execute_query = AsyncMock(side_effect=[[{"record": ref}], []])

        assert await provider.find_next_queued_duplicate("r1") is None

        queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
        assert len(queries) == 2, "a trashed reference record still leads to its live duplicates"
        live = aql_live_record("record") if backend == "arango" else cypher_live_record("record")
        assert live not in queries[0]
        assert live in queries[1]

    async def test_status_is_copied_only_onto_live_duplicates(self, backend) -> None:
        provider = _arango() if backend == "arango" else _neo4j()
        ref = {"_key": "r1", "id": "r1", "md5Checksum": "md5", "orgId": "o1"}
        if backend == "arango":
            provider.http_client.execute_aql = AsyncMock(side_effect=[[ref], []])
        else:
            provider.client.execute_query = AsyncMock(side_effect=[[{"record": ref}], []])

        await provider.update_queued_duplicates_status("r1", "COMPLETED", "vr1")

        queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
        live = aql_live_record("record") if backend == "arango" else cypher_live_record("record")
        assert live in queries[1]


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
class TestFailedRecords:
    async def test_failed_by_org_leaves_out_the_trash(self, backend) -> None:
        provider = _arango() if backend == "arango" else _neo4j()
        provider.get_nodes_by_filters = AsyncMock(
            return_value=[
                {"_key": "live", "indexingStatus": "FAILED"},
                {"_key": "legacy", "indexingStatus": "FAILED", "isDeleted": False},
                {"_key": "trashed", "indexingStatus": "FAILED", "isDeleted": True},
            ]
        )
        got = await provider.get_failed_records_by_org("o1", "c1")
        assert [r["_key"] for r in got] == ["live", "legacy"]

    async def test_failed_with_users_is_live_only(self, backend) -> None:
        provider = _arango() if backend == "arango" else _neo4j()
        await provider.get_failed_records_with_active_users("o1", "c1")
        queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
        live = aql_live_record("doc") if backend == "arango" else cypher_live_record("record")
        assert live in queries[0]


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_weburl_lookup_is_live_only(backend) -> None:
    """Resolves agent references and cross-connector links."""
    provider = _arango() if backend == "arango" else _neo4j()
    await provider.get_record_by_weburl("https://x", "o1")
    queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
    live = aql_live_record("record") if backend == "arango" else "(r.isDeleted IS NULL OR r.isDeleted = false)"
    assert live in queries[0]


class TestNeo4jKnowledgeHubBrowse:
    """Arango's browse queries already skipped the trash; Neo4j's did not."""

    def test_folder_children_and_their_counts(self) -> None:
        cypher = _neo4j()._get_record_children_cypher()
        assert cypher_live_record("record") in cypher
        assert cypher_live_record("child") in cypher

    def test_kb_root_and_group_children(self) -> None:
        provider = _neo4j()
        provider._get_permission_role_cypher = MagicMock(return_value="")
        app_cypher = provider._get_app_children_cypher()
        assert cypher_live_record("record") in app_cypher
        assert cypher_live_record("child") in app_cypher
        assert cypher_live_record("child_record") in app_cypher
        rg_cypher = provider._get_record_group_children_cypher("recordGroup")
        for var in ("record", "internal_record", "child", "child_record_check"):
            assert cypher_live_record(var) in rg_cypher, var


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
async def test_connector_delete_still_finds_trashed_records(backend) -> None:
    """Connector delete is a hard delete of everything, trash included."""
    provider = _arango() if backend == "arango" else _neo4j()
    if backend == "neo4j":
        provider.client.execute_query = AsyncMock(
            return_value=[{"result": {"record_keys": [], "record_ids": [], "record_group_keys": [],
                                      "role_keys": [], "group_keys": [], "virtual_record_ids": [],
                                      "all_node_ids": []}}]
        )
    await provider._collect_connector_entities("c1")
    queries = _arango_queries(provider) if backend == "arango" else _neo4j_queries(provider)
    assert queries
    assert not any("isDeleted" in q for q in queries)


@pytest.mark.parametrize("backend", ["arango", "neo4j"])
@pytest.mark.parametrize("method", ["delete_record_by_external_id", "remove_user_access_to_record"])
async def test_connector_writes_look_up_trashed_records(backend, method) -> None:
    """The source removed the item; a trashed copy must still be found to act on."""
    provider = _arango() if backend == "arango" else _neo4j()
    provider.get_record_by_external_id = AsyncMock(return_value=None)
    await getattr(provider, method)("c1", "e1", "u1")
    assert provider.get_record_by_external_id.await_args.kwargs["visibility"] is RecordVisibility.ALL


async def test_graph_data_store_upsert_lookup_sees_the_trash() -> None:
    """Sync decides create-or-update on this answer, so it must see trashed records."""
    graph = MagicMock()
    graph.get_record_by_external_id = AsyncMock(return_value=None)
    store = GraphTransactionStore(graph, "txn-1")
    await store.get_record_by_external_id("c1", "e1")
    assert graph.get_record_by_external_id.await_args.kwargs["visibility"] is RecordVisibility.ALL
