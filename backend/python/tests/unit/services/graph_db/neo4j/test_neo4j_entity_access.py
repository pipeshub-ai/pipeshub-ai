"""Neo4j entity-access queries: permission scoping for knowledge-graph entity search.

These run against a mocked client, so they pin the contract callers rely on
(fail-closed errors, every ref present in the result, org/connector filters in
the Cypher) rather than Cypher semantics.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors, PermissionModel
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider(rows: list[dict] | Exception | None = None) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    if isinstance(rows, Exception):
        p.client.execute_query = AsyncMock(side_effect=rows)
    else:
        p.client.execute_query = AsyncMock(return_value=rows or [])
    return p


def _queries(p: Neo4jProvider) -> list[str]:
    return [c.args[0] for c in p.client.execute_query.call_args_list]


class TestGetEntityAccessContext:
    @pytest.mark.asyncio
    async def test_unknown_user_returns_none(self) -> None:
        p = _provider([])
        assert await p.get_entity_access_context("u1", "org1") is None

    @pytest.mark.asyncio
    async def test_returns_shaped_context(self) -> None:
        app = {"id": "a1", "name": "Drive", "type": "DRIVE", "permissionModel": None}
        p = _provider([{"user_key": "uk1", "apps": [app], "record_group_ids": ["rg1", "rg2"]}])
        ctx = await p.get_entity_access_context("u1", "org1")
        assert ctx == {"user_key": "uk1", "apps": [app], "record_group_ids": ["rg1", "rg2"]}

    @pytest.mark.asyncio
    async def test_binds_parameters_and_transaction(self) -> None:
        p = _provider([])
        await p.get_entity_access_context("u1", "org1", transaction="txn-1")
        call = p.client.execute_query.call_args
        assert call.kwargs["parameters"] == {
            "user_id": "u1",
            "org_id": "org1",
            "source_ids": [],
            "kb_type": Connectors.KNOWLEDGE_BASE.value,
            "app_level": PermissionModel.APP_LEVEL.value,
        }
        assert call.kwargs["txn_id"] == "txn-1"

    @pytest.mark.asyncio
    async def test_source_ids_are_bound(self) -> None:
        p = _provider([])
        await p.get_entity_access_context("u1", "org1", ["a1", "kb1"])
        assert p.client.execute_query.call_args.kwargs["parameters"]["source_ids"] == ["a1", "kb1"]

    @pytest.mark.asyncio
    async def test_query_applies_record_group_scoping(self) -> None:
        p = _provider([])
        await p.get_entity_access_context("u1", "org1")
        query = _queries(p)[0]
        for fragment in (
            "isDeleted",
            "connectorId IN record_level_app_ids",
            "hideChildren",
            "INHERIT_PERMISSIONS*1..5",
            "{type: 'TEAM'}",
            "{type: 'ORG'}",
            "USER_APP_RELATION",
            "rg.orgId = $org_id",
            "child.orgId = $org_id",
        ):
            assert fragment in query, fragment

    @pytest.mark.asyncio
    async def test_query_failure_propagates(self) -> None:
        """Callers fail closed; swallowing would read as "no access"."""
        p = _provider(RuntimeError("neo4j down"))
        with pytest.raises(RuntimeError, match="neo4j down"):
            await p.get_entity_access_context("u1", "org1")

    @pytest.mark.asyncio
    async def test_missing_client_raises(self) -> None:
        p = _provider([])
        p.client = None
        with pytest.raises(RuntimeError):
            await p.get_entity_access_context("u1", "org1")


class TestGetEntityCandidateRecords:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "refs, org_id",
        [([], "org1"), ([{"id": "t1", "type": "topic", "connectorIds": ["c1"]}], "")],
    )
    async def test_empty_refs_or_org_issue_no_query(self, refs, org_id) -> None:
        p = _provider([])
        assert await p.get_entity_candidate_records(refs, org_id) == {}
        p.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_query_per_entity_type(self) -> None:
        p = _provider([])
        await p.get_entity_candidate_records(
            [
                {"id": "t1", "type": "topic", "connectorIds": ["c1"]},
                {"id": "t2", "type": "topic", "connectorIds": ["c1"]},
                {"id": "t1", "type": "topic", "connectorIds": ["c2"]},
                {"id": "rg1", "type": "record_group", "connectorIds": ["c1"]},
                {"id": "r1", "type": "record", "connectorIds": ["c1"]},
            ],
            "org1",
        )
        assert p.client.execute_query.await_count == 3
        topic_params = next(
            c.kwargs["parameters"]
            for c in p.client.execute_query.call_args_list
            if ":Topics" in c.args[0]
        )
        assert [r["id"] for r in topic_params["refs"]] == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_subcategory_matches_every_level(self) -> None:
        p = _provider([])
        await p.get_entity_candidate_records(
            [{"id": "s1", "type": "subcategory", "connectorIds": ["c1"]}], "org1"
        )
        query = _queries(p)[0]
        for label in ("Subcategories1", "Subcategories2", "Subcategories3"):
            assert f":{label} " in query
        assert "BELONGS_TO_CATEGORY" in query

    @pytest.mark.asyncio
    async def test_record_group_is_org_scoped(self) -> None:
        p = _provider([])
        await p.get_entity_candidate_records(
            [{"id": "rg1", "type": "record_group", "connectorIds": ["c1"]}], "org1"
        )
        query = _queries(p)[0]
        assert "e.orgId = $org_id" in query
        assert "(rec:Record)-[:BELONGS_TO]->(e)" in query

    @pytest.mark.asyncio
    async def test_binds_paging_and_record_types(self) -> None:
        p = _provider([])
        await p.get_entity_candidate_records(
            [{"id": "d1", "type": "department", "connectorIds": ["c1"]}],
            "org1",
            record_types=["FILE"],
            limit_per_entity=5,
            offset=10,
            transaction="txn-1",
        )
        call = p.client.execute_query.call_args
        assert call.kwargs["parameters"] == {
            "refs": [{"id": "d1", "connectorIds": ["c1"]}],
            "org_id": "org1",
            "record_types": ["FILE"],
            "offset": 10,
            "limit": 5,
        }
        assert call.kwargs["txn_id"] == "txn-1"
        assert "rec.connectorId IN ref.connectorIds" in call.args[0]

    @pytest.mark.asyncio
    async def test_unsupported_types_and_empty_ids_are_skipped(self) -> None:
        p = _provider([])
        out = await p.get_entity_candidate_records(
            [
                {"id": "x1", "type": "connector", "connectorIds": ["c1"]},
                {"id": "", "type": "topic", "connectorIds": ["c1"]},
            ],
            "org1",
        )
        assert out == {}
        p.client.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_every_queried_ref_is_present(self) -> None:
        row = {"_key": "rec1", "recordName": "Doc", "connectorId": "c1"}
        p = _provider([{"id": "t1", "rows": [row]}])
        out = await p.get_entity_candidate_records(
            [
                {"id": "t1", "type": "topic", "connectorIds": ["c1"]},
                {"id": "t2", "type": "topic", "connectorIds": ["c1"]},
            ],
            "org1",
        )
        assert out == {"t1": [row], "t2": []}

    @pytest.mark.asyncio
    async def test_query_failure_propagates(self) -> None:
        p = _provider(RuntimeError("neo4j down"))
        with pytest.raises(RuntimeError, match="neo4j down"):
            await p.get_entity_candidate_records(
                [{"id": "t1", "type": "topic", "connectorIds": ["c1"]}], "org1"
            )


_ALL_METADATA_FILTERS = {
    "departments": ["Engineering"],
    "categories": ["Finance"],
    "subcategories1": ["Tax"],
    "subcategories2": ["VAT"],
    "subcategories3": ["EU"],
    "languages": ["en"],
    "topics": ["Budget"],
}


class TestMetadataFilterLabels:
    """The filters used to match labels that no node carries, so any metadata
    filter silently returned zero records."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method, args",
        [
            ("_get_virtual_ids_for_connector", ("u1", "org1", "c1")),
            ("_get_kb_virtual_ids", ("u1", "org1", None)),
        ],
    )
    async def test_filters_use_real_taxonomy_labels(self, method, args) -> None:
        p = _provider([])
        await getattr(p, method)(*args, _ALL_METADATA_FILTERS)
        query = _queries(p)[0]
        for label in (
            ":Departments)",
            ":Categories)",
            ":Subcategories1)",
            ":Subcategories2)",
            ":Subcategories3)",
            ":Languages)",
            ":Topics)",
        ):
            assert label in query, label
        for stale in (":Department)", ":Category)", ":Language)", ":Topic)"):
            assert stale not in query, stale


class TestFilterNodesWithPermissionRole:
    _NODES = [{"id": "r1", "type": "record"}]

    @pytest.mark.asyncio
    async def test_failure_is_swallowed_by_default(self) -> None:
        p = _provider(RuntimeError("neo4j down"))
        assert await p.filter_nodes_with_permission_role(self._NODES, "uk1", "org1") == set()

    @pytest.mark.asyncio
    async def test_failure_is_raised_when_requested(self) -> None:
        p = _provider(RuntimeError("neo4j down"))
        with pytest.raises(RuntimeError, match="neo4j down"):
            await p.filter_nodes_with_permission_role(
                self._NODES, "uk1", "org1", raise_on_error=True
            )

    @pytest.mark.asyncio
    async def test_missing_client_raises_only_when_requested(self) -> None:
        p = _provider([])
        p.client = None
        assert await p.filter_nodes_with_permission_role(self._NODES, "uk1", "org1") == set()
        with pytest.raises(RuntimeError):
            await p.filter_nodes_with_permission_role(
                self._NODES, "uk1", "org1", raise_on_error=True
            )
