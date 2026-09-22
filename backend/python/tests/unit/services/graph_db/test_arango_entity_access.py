"""ArangoHTTPProvider entity-access helpers: get_entity_access_context,
get_entity_candidate_records, and filter_nodes_with_permission_role's
raise_on_error flag."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors, PermissionModel
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


@pytest.fixture
def provider() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.execute_query = AsyncMock(return_value=[])
    return p


def _queries(provider: ArangoHTTPProvider) -> list[str]:
    return [c.args[0] for c in provider.execute_query.await_args_list]


def _bind_vars(provider: ArangoHTTPProvider) -> list[dict]:
    return [c.kwargs["bind_vars"] for c in provider.execute_query.await_args_list]


class TestGetEntityAccessContext:
    @pytest.mark.asyncio
    async def test_returns_none_when_user_missing(self, provider) -> None:
        assert await provider.get_entity_access_context("u1", "org1") is None

    @pytest.mark.asyncio
    async def test_returns_row(self, provider) -> None:
        row = {
            "user_key": "uk1",
            "apps": [{"id": "a1", "name": "Drive", "type": "DRIVE", "permissionModel": None}],
            "record_group_ids": ["rg1"],
        }
        provider.execute_query.return_value = [row]
        assert await provider.get_entity_access_context("u1", "org1") == row

    @pytest.mark.asyncio
    async def test_bind_vars(self, provider) -> None:
        await provider.get_entity_access_context("u1", "org1", transaction="txn")
        call = provider.execute_query.await_args
        assert call.kwargs["transaction"] == "txn"
        assert call.kwargs["bind_vars"] == {
            "user_id": "u1",
            "org_id": "org1",
            "source_ids": [],
            "kb_type": Connectors.KNOWLEDGE_BASE.value,
            "app_level": PermissionModel.APP_LEVEL.value,
        }

        await provider.get_entity_access_context("u1", "org1", source_ids=["a1"])
        assert provider.execute_query.await_args.kwargs["bind_vars"]["source_ids"] == ["a1"]

    @pytest.mark.asyncio
    async def test_query_applies_access_filters(self, provider) -> None:
        await provider.get_entity_access_context("u1", "org1")
        query = _queries(provider)[0]
        for fragment in (
            "u.userId == @user_id",
            "rg.isDeleted != true",
            "child.isDeleted != true",
            "rg.connectorId IN record_level_app_ids",
            "child.connectorId IN record_level_app_ids",
            "seed.hideChildren != true",
            "INBOUND seed._id inheritPermissions",
            "PRUNE child.orgId != @org_id",
            '"TEAM"',
            '"ORG"',
            '"GROUP"',
            "app.type == @kb_type",
            "app.permissionModel != @app_level",
        ):
            assert fragment in query, fragment

    @pytest.mark.asyncio
    async def test_propagates_query_error(self, provider) -> None:
        provider.execute_query.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            await provider.get_entity_access_context("u1", "org1")


class TestGetEntityCandidateRecords:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "refs,org_id",
        [([], "org1"), ([{"id": "t1", "type": "topic", "connectorIds": ["c1"]}], "")],
    )
    async def test_empty_input_skips_query(self, provider, refs, org_id) -> None:
        assert await provider.get_entity_candidate_records(refs, org_id) == {}
        provider.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_one_query_per_type_and_merged_result(self, provider) -> None:
        row = {"_key": "r1", "recordName": "Doc"}
        provider.execute_query.side_effect = [
            [{"id": "t1", "rows": [row]}, {"id": "t2", "rows": []}],
            [{"id": "rg1", "rows": []}],
            [{"id": "r9", "rows": [row]}],
        ]
        refs = [
            {"id": "t1", "type": "topic", "connectorIds": ["c1"]},
            {"id": "rg1", "type": "record_group", "connectorIds": ["c1"]},
            {"id": "t2", "type": "topic", "connectorIds": ["c2"]},
            {"id": "r9", "type": "record", "connectorIds": ["c1"]},
        ]
        result = await provider.get_entity_candidate_records(refs, "org1")

        assert provider.execute_query.await_count == 3
        assert result == {"t1": [row], "t2": [], "rg1": [], "r9": [row]}
        topic_vars = _bind_vars(provider)[0]
        assert topic_vars["refs"] == [
            {"id": "t1", "connectorIds": ["c1"]},
            {"id": "t2", "connectorIds": ["c2"]},
        ]

    @pytest.mark.asyncio
    async def test_subcategory_targets_all_levels(self, provider) -> None:
        await provider.get_entity_candidate_records(
            [{"id": "s1", "type": "subcategory", "connectorIds": ["c1"]}], "org1"
        )
        query = _queries(provider)[0]
        assert "FOR edge IN belongsToCategory" in query
        for collection in ("subcategories1", "subcategories2", "subcategories3"):
            assert f'CONCAT("{collection}/", ref.id)' in query
        assert 'CONCAT("categories/"' not in query

    @pytest.mark.asyncio
    async def test_record_group_query_checks_group_org(self, provider) -> None:
        await provider.get_entity_candidate_records(
            [{"id": "rg1", "type": "record_group", "connectorIds": ["c1"]}], "org1"
        )
        query = _queries(provider)[0]
        assert "FOR edge IN belongsTo\n" in query
        assert "rg.orgId == @org_id" in query

    @pytest.mark.asyncio
    async def test_record_types_filter_only_when_given(self, provider) -> None:
        refs = [
            {"id": "t1", "type": "topic", "connectorIds": ["c1"]},
            {"id": "r1", "type": "record", "connectorIds": ["c1"]},
        ]
        await provider.get_entity_candidate_records(refs, "org1")
        assert all("@record_types" not in q for q in _queries(provider))
        assert all("record_types" not in bv for bv in _bind_vars(provider))

        provider.execute_query.reset_mock()
        await provider.get_entity_candidate_records(refs, "org1", record_types=["FILE"])
        assert all("recordType IN @record_types" in q for q in _queries(provider))
        assert all(bv["record_types"] == ["FILE"] for bv in _bind_vars(provider))

    @pytest.mark.asyncio
    async def test_offset_and_limit_bound(self, provider) -> None:
        refs = [
            {"id": "t1", "type": "topic", "connectorIds": ["c1"]},
            {"id": "r1", "type": "record", "connectorIds": ["c1"]},
        ]
        await provider.get_entity_candidate_records(refs, "org1", limit_per_entity=5, offset=10)
        topic_vars, record_vars = _bind_vars(provider)
        assert topic_vars["offset"] == 10 and topic_vars["limit"] == 5
        assert "LIMIT @offset, @limit" in _queries(provider)[0]
        # The record query has no LIMIT, and Arango rejects unused bind vars.
        assert record_vars["offset"] == 10 and "limit" not in record_vars

        provider.execute_query.reset_mock()
        await provider.get_entity_candidate_records(refs[:1], "org1", limit_per_entity=0, offset=-3)
        assert _bind_vars(provider)[0]["offset"] == 0
        assert _bind_vars(provider)[0]["limit"] == 1

    @pytest.mark.asyncio
    async def test_unsupported_type_and_missing_id_skipped(self, provider) -> None:
        result = await provider.get_entity_candidate_records(
            [
                {"id": "x1", "type": "connector", "connectorIds": ["c1"]},
                {"id": "", "type": "topic", "connectorIds": ["c1"]},
            ],
            "org1",
        )
        assert result == {}
        provider.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ref_without_connectors_maps_to_empty_without_widening(self, provider) -> None:
        provider.execute_query.return_value = [{"id": "t2", "rows": [{"_key": "r1"}]}]
        result = await provider.get_entity_candidate_records(
            [
                {"id": "t1", "type": "topic", "connectorIds": []},
                {"id": "t2", "type": "topic", "connectorIds": ["c1"]},
            ],
            "org1",
        )
        assert result == {"t1": [], "t2": [{"_key": "r1"}]}
        assert _bind_vars(provider)[0]["refs"] == [{"id": "t2", "connectorIds": ["c1"]}]

    @pytest.mark.asyncio
    async def test_all_refs_without_connectors_skip_query(self, provider) -> None:
        result = await provider.get_entity_candidate_records(
            [{"id": "t1", "type": "topic", "connectorIds": []}], "org1"
        )
        assert result == {"t1": []}
        provider.execute_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refs_present_when_query_returns_nothing(self, provider) -> None:
        result = await provider.get_entity_candidate_records(
            [
                {"id": "d1", "type": "department", "connectorIds": ["c1"]},
                {"id": "l1", "type": "language", "connectorIds": ["c1"]},
            ],
            "org1",
        )
        assert result == {"d1": [], "l1": []}

    @pytest.mark.asyncio
    async def test_propagates_query_error(self, provider) -> None:
        provider.execute_query.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            await provider.get_entity_candidate_records(
                [{"id": "t1", "type": "topic", "connectorIds": ["c1"]}], "org1"
            )


class TestFilterNodesWithPermissionRoleRaiseOnError:
    NODES = [{"id": "r1", "type": "record"}]

    @pytest.mark.asyncio
    async def test_default_swallows_error(self, provider) -> None:
        provider.http_client.execute_aql.side_effect = RuntimeError("boom")
        assert await provider.filter_nodes_with_permission_role(self.NODES, "uk1", "org1") == set()
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_raise_on_error_reraises(self, provider) -> None:
        provider.http_client.execute_aql.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            await provider.filter_nodes_with_permission_role(
                self.NODES, "uk1", "org1", raise_on_error=True
            )
        provider.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_client(self, provider) -> None:
        provider.http_client = None
        assert await provider.filter_nodes_with_permission_role(self.NODES, "uk1", "org1") == set()
        with pytest.raises(RuntimeError):
            await provider.filter_nodes_with_permission_role(
                self.NODES, "uk1", "org1", raise_on_error=True
            )
