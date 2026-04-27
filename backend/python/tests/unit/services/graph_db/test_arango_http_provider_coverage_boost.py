"""
Additional unit tests for ArangoHTTPProvider targeting under-covered methods.

Focus areas (previously uncovered):
- ensure_all_team_with_users
- add_user_to_all_team
- ensure_team_app_edge
- get_accessible_virtual_record_ids
- _get_virtual_ids_for_connector
- _get_kb_virtual_ids
- get_record_by_issue_key (success path)
"""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


@pytest.fixture
def mock_logger():
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def mock_config_service():
    cs = AsyncMock()
    cs.get_config = AsyncMock(return_value={
        "url": "http://localhost:8529",
        "username": "root",
        "password": "secret",
        "db": "test_db",
    })
    return cs


@pytest.fixture
def provider(mock_logger, mock_config_service):
    p = ArangoHTTPProvider(mock_logger, mock_config_service)
    p.http_client = AsyncMock()
    return p


# ---------------------------------------------------------------------------
# ensure_all_team_with_users
# ---------------------------------------------------------------------------


class TestEnsureAllTeamWithUsers:
    @pytest.mark.asyncio
    async def test_no_active_users_returns_early(self, provider):
        """No active users found -> method returns without edge creation."""
        provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        provider.get_users = AsyncMock(return_value=[])
        provider.batch_upsert_nodes = AsyncMock()
        provider.batch_create_edges = AsyncMock()

        await provider.ensure_all_team_with_users("org1")

        provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_team_when_missing_and_adds_first_user_as_owner(self, provider):
        """Team missing + first user -> creates team, assigns OWNER, updates createdBy."""
        provider.get_document = AsyncMock(return_value=None)
        provider.batch_upsert_nodes = AsyncMock()
        provider.get_users = AsyncMock(return_value=[
            {"_key": "u1", "createdAtTimestamp": 100},
        ])
        provider.get_team_with_users = AsyncMock(return_value={"members": []})
        provider.get_edge = AsyncMock(return_value=None)
        provider.update_node = AsyncMock()
        provider.batch_create_edges = AsyncMock()

        await provider.ensure_all_team_with_users("org1")

        # Team created
        provider.batch_upsert_nodes.assert_awaited()
        # createdBy updated with first user
        provider.update_node.assert_awaited_once()
        # Exactly one permission edge created
        assert provider.batch_create_edges.await_count == 1
        edge = provider.batch_create_edges.await_args[0][0][0]
        assert edge["role"] == "OWNER"
        assert edge["from_id"] == "u1"
        assert edge["to_id"] == "all_org1"

    @pytest.mark.asyncio
    async def test_existing_team_and_members_assigns_reader(self, provider):
        """Existing team with members -> new user gets READER."""
        provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        provider.batch_upsert_nodes = AsyncMock()
        provider.get_users = AsyncMock(return_value=[
            {"_key": "u2", "createdAtTimestamp": 200},
        ])
        provider.get_team_with_users = AsyncMock(return_value={
            "members": [{"userEmail": "old@x", "role": "OWNER"}]
        })
        provider.get_edge = AsyncMock(return_value=None)
        provider.update_node = AsyncMock()
        provider.batch_create_edges = AsyncMock()

        await provider.ensure_all_team_with_users("org1")

        # Team already existed -> upsert not called
        provider.batch_upsert_nodes.assert_not_awaited()
        # OWNER already assigned -> update_node not called
        provider.update_node.assert_not_awaited()
        edge = provider.batch_create_edges.await_args[0][0][0]
        assert edge["role"] == "READER"

    @pytest.mark.asyncio
    async def test_user_with_existing_edge_is_skipped(self, provider):
        """User with existing permission edge -> no new edge created for them."""
        provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        provider.get_users = AsyncMock(return_value=[
            {"_key": "u1", "createdAtTimestamp": 100},
            {"_key": "u2", "createdAtTimestamp": 200},
        ])
        provider.get_team_with_users = AsyncMock(return_value={"members": []})
        # u1 already has an edge; u2 does not
        provider.get_edge = AsyncMock(side_effect=[{"role": "OWNER"}, None])
        provider.update_node = AsyncMock()
        provider.batch_create_edges = AsyncMock()

        await provider.ensure_all_team_with_users("org1")

        # Only one new edge created (for u2 only)
        assert provider.batch_create_edges.await_count == 1
        edge = provider.batch_create_edges.await_args[0][0][0]
        assert edge["from_id"] == "u2"
        # u2 became the first OWNER because members list was empty
        assert edge["role"] == "OWNER"

    @pytest.mark.asyncio
    async def test_user_without_key_is_skipped(self, provider):
        """User dict missing both _key and id is skipped."""
        provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        provider.get_users = AsyncMock(return_value=[{"createdAtTimestamp": 1}])
        provider.get_team_with_users = AsyncMock(return_value={"members": []})
        provider.get_edge = AsyncMock(return_value=None)
        provider.batch_create_edges = AsyncMock()

        await provider.ensure_all_team_with_users("org1")

        provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_node_failure_is_swallowed(self, provider):
        """Failure in update_node during owner assignment is logged, not raised."""
        provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        provider.get_users = AsyncMock(return_value=[
            {"_key": "u1", "createdAtTimestamp": 1},
        ])
        provider.get_team_with_users = AsyncMock(return_value={"members": []})
        provider.get_edge = AsyncMock(return_value=None)
        provider.update_node = AsyncMock(side_effect=Exception("update failed"))
        provider.batch_create_edges = AsyncMock()

        # Should not raise
        await provider.ensure_all_team_with_users("org1")
        provider.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_top_level_exception_propagates(self, provider):
        """Exception in get_users propagates."""
        provider.get_document = AsyncMock(side_effect=Exception("boom"))
        with pytest.raises(Exception):
            await provider.ensure_all_team_with_users("org1")


# ---------------------------------------------------------------------------
# add_user_to_all_team
# ---------------------------------------------------------------------------


class TestAddUserToAllTeam:
    @pytest.mark.asyncio
    async def test_user_already_has_edge_returns_early(self, provider):
        provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        provider.get_edge = AsyncMock(return_value={"role": "READER"})
        provider.batch_create_edges = AsyncMock()

        await provider.add_user_to_all_team("org1", "u1")

        provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_team_and_assigns_owner_when_empty(self, provider):
        provider.get_document = AsyncMock(return_value=None)
        provider.batch_upsert_nodes = AsyncMock()
        provider.get_edge = AsyncMock(return_value=None)
        provider.get_team_with_users = AsyncMock(return_value={"members": []})
        provider.update_node = AsyncMock()
        provider.batch_create_edges = AsyncMock()

        await provider.add_user_to_all_team("org1", "u1")

        provider.batch_upsert_nodes.assert_awaited()  # team created
        provider.update_node.assert_awaited_once()  # createdBy updated
        edge = provider.batch_create_edges.await_args[0][0][0]
        assert edge["role"] == "OWNER"
        assert edge["from_id"] == "u1"

    @pytest.mark.asyncio
    async def test_reader_when_team_has_members(self, provider):
        provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        provider.get_edge = AsyncMock(return_value=None)
        provider.get_team_with_users = AsyncMock(return_value={"members": [{}]})
        provider.update_node = AsyncMock()
        provider.batch_create_edges = AsyncMock()

        await provider.add_user_to_all_team("org1", "u2")

        provider.update_node.assert_not_awaited()
        edge = provider.batch_create_edges.await_args[0][0][0]
        assert edge["role"] == "READER"

    @pytest.mark.asyncio
    async def test_update_node_failure_is_swallowed(self, provider):
        provider.get_document = AsyncMock(return_value={"_key": "all_org1"})
        provider.get_edge = AsyncMock(return_value=None)
        provider.get_team_with_users = AsyncMock(return_value={"members": []})
        provider.update_node = AsyncMock(side_effect=Exception("update failed"))
        provider.batch_create_edges = AsyncMock()

        await provider.add_user_to_all_team("org1", "u1")
        provider.batch_create_edges.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_propagates(self, provider):
        provider.get_document = AsyncMock(side_effect=Exception("boom"))
        with pytest.raises(Exception):
            await provider.add_user_to_all_team("org1", "u1")


# ---------------------------------------------------------------------------
# ensure_team_app_edge
# ---------------------------------------------------------------------------


class TestEnsureTeamAppEdge:
    @pytest.mark.asyncio
    async def test_existing_edge_skips_creation(self, provider):
        provider.get_edge = AsyncMock(return_value={"sourceUserId": "all_org1"})
        provider.batch_create_edges = AsyncMock()

        await provider.ensure_team_app_edge("connector_x", "org1")

        provider.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_edge_when_missing(self, provider):
        provider.get_edge = AsyncMock(return_value=None)
        provider.batch_create_edges = AsyncMock()

        await provider.ensure_team_app_edge("connector_x", "org1", transaction="txn1")

        assert provider.batch_create_edges.await_count == 1
        args, kwargs = provider.batch_create_edges.await_args
        edge = args[0][0]
        assert edge["sourceUserId"] == "all_org1"
        assert edge["_from"].endswith("/all_org1")
        assert edge["_to"].endswith("/connector_x")
        assert kwargs.get("transaction") == "txn1"

    @pytest.mark.asyncio
    async def test_exception_propagates(self, provider):
        provider.get_edge = AsyncMock(side_effect=Exception("db down"))
        with pytest.raises(Exception):
            await provider.ensure_team_app_edge("c1", "org1")


# ---------------------------------------------------------------------------
# get_accessible_virtual_record_ids
# ---------------------------------------------------------------------------


class TestGetAccessibleVirtualRecordIds:
    @pytest.mark.asyncio
    async def test_no_inputs_returns_empty(self, provider):
        provider._get_user_app_ids = AsyncMock(return_value=[])
        result = await provider.get_accessible_virtual_record_ids("u1", "org1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_knowledge_base_prefix_connector_skipped(self, provider):
        """connector_id starting with 'knowledgeBase_' is filtered out, leaving no tasks."""
        provider._get_user_app_ids = AsyncMock(return_value=["knowledgeBase_abc"])
        result = await provider.get_accessible_virtual_record_ids(
            "u1", "org1", filters={"apps": ["knowledgeBase_abc"]}
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_merges_results_across_tasks(self, provider):
        """Results from connector + kb paths are merged; first wins on duplicates."""
        async def fake_connector(user, org, cid, metadata):
            return {"v1": "r1", "v2": "r2"}

        async def fake_kb(user, org, kb_ids, metadata):
            # v2 is a duplicate with a different recordId; existing mapping wins
            return {"v2": "OVERRIDE", "v3": "r3"}

        provider._get_user_app_ids = AsyncMock(return_value=["c1"])
        provider._get_virtual_ids_for_connector = fake_connector
        provider._get_kb_virtual_ids = fake_kb

        result = await provider.get_accessible_virtual_record_ids(
            "u1", "org1", filters={"apps": ["c1"], "kb": ["kb1"]}
        )
        assert result["v1"] == "r1"
        assert result["v2"] == "r2"  # first-wins
        assert result["v3"] == "r3"

    @pytest.mark.asyncio
    async def test_task_exceptions_are_tolerated(self, provider):
        async def failing(user, org, cid, metadata):
            raise RuntimeError("boom")

        async def ok(user, org, kb_ids, metadata):
            return {"v1": "r1"}

        provider._get_user_app_ids = AsyncMock(return_value=["c1"])
        provider._get_virtual_ids_for_connector = failing
        provider._get_kb_virtual_ids = ok

        result = await provider.get_accessible_virtual_record_ids(
            "u1", "org1", filters={"apps": ["c1"], "kb": ["kb1"]}
        )
        assert result == {"v1": "r1"}

    @pytest.mark.asyncio
    async def test_top_level_exception_returns_empty(self, provider):
        """If an error occurs inside the try block, the method returns {}."""
        provider._get_user_app_ids = AsyncMock(side_effect=Exception("boom"))
        result = await provider.get_accessible_virtual_record_ids("u1", "org1")
        assert result == {}


# ---------------------------------------------------------------------------
# _get_virtual_ids_for_connector
# ---------------------------------------------------------------------------


class TestGetAllVirtualIdsForConnector:
    @pytest.mark.asyncio
    async def test_success_builds_mapping(self, provider):
        provider.execute_query = AsyncMock(return_value=[
            {"virtualRecordId": "v1", "recordId": "r1"},
            {"virtualRecordId": "v2", "recordId": "r2"},
            # Filtered by truthiness checks:
            {"virtualRecordId": None, "recordId": "rX"},
            {"virtualRecordId": "v3", "recordId": None},
            None,
        ])
        result = await provider._get_virtual_ids_for_connector("u1", "org1", "c1")
        assert result == {"v1": "r1", "v2": "r2"}

    @pytest.mark.asyncio
    async def test_empty_results(self, provider):
        provider.execute_query = AsyncMock(return_value=[])
        result = await provider._get_virtual_ids_for_connector("u1", "org1", "c1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_none_results(self, provider):
        provider.execute_query = AsyncMock(return_value=None)
        result = await provider._get_virtual_ids_for_connector("u1", "org1", "c1")
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, provider):
        provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await provider._get_virtual_ids_for_connector("u1", "org1", "c1")
        assert result == {}


# ---------------------------------------------------------------------------
# _get_kb_virtual_ids
# ---------------------------------------------------------------------------


class TestGetAllKbVirtualIds:
    @pytest.mark.asyncio
    async def test_success_builds_mapping(self, provider):
        provider.execute_query = AsyncMock(return_value=[
            {"virtualRecordId": "v1", "recordId": "r1"},
            {"virtualRecordId": "", "recordId": "rX"},  # filtered
        ])
        result = await provider._get_kb_virtual_ids("u1", "org1", ["kb1", "kb2"])
        assert result == {"v1": "r1"}

    @pytest.mark.asyncio
    async def test_empty_results(self, provider):
        provider.execute_query = AsyncMock(return_value=[])
        result = await provider._get_kb_virtual_ids("u1", "org1", ["kb1"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self, provider):
        provider.execute_query = AsyncMock(side_effect=Exception("db error"))
        result = await provider._get_kb_virtual_ids("u1", "org1", ["kb1"])
        assert result == {}


# ---------------------------------------------------------------------------
# get_record_by_issue_key (success path)
# ---------------------------------------------------------------------------


class TestGetRecordByIssueKey:
    @pytest.mark.asyncio
    async def test_success_returns_typed_record(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[{
            "record": {"_key": "r1", "recordType": "TICKET"},
            "ticket": {"type": "Epic"},
        }])
        provider._create_typed_record_from_arango = MagicMock(return_value="typed_record")

        result = await provider.get_record_by_issue_key("c1", "PROJ-123")
        assert result == "typed_record"
        provider._create_typed_record_from_arango.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_results_returns_none(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[])
        result = await provider.get_record_by_issue_key("c1", "PROJ-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_first_result_none_returns_none(self, provider):
        provider.http_client.execute_aql = AsyncMock(return_value=[None])
        result = await provider.get_record_by_issue_key("c1", "PROJ-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, provider):
        provider.http_client.execute_aql = AsyncMock(side_effect=Exception("db"))
        result = await provider.get_record_by_issue_key("c1", "PROJ-123")
        assert result is None
